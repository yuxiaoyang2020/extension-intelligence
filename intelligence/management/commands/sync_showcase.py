"""
sync_showcase —— 数据填充专用命令，一次性同步能让 Home 页各个板块都有真实
数据可看的一批插件，而不是像 sync_top_growth 那样只覆盖"7日增长最高"一种。

背景：Home 页除了"7日增长排行预览"，还有4类机会信号（新兴增长/增长加速/
低关注高增长/快速里程碑）+ "最近突破"/"不只是看到今天"/"案例研究"——后3+1个
依赖 ExtensionChart（历史曲线+里程碑），需要对每个插件额外跑一次约1分钟的
plugin_history 现场扫描，不可能对全部插件都跑，只挑一小批有代表性的。

用法：
    python manage.py sync_showcase
    python manage.py sync_showcase --chart-count 10   # 更多插件带历史曲线（更慢）

策略：
    1. 只算一次全量 metrics（跟 sync_top_growth 一样）。
    2. 按 views.py 首页用的同一套筛选条件（7日增长Top / 新兴增长 / 增长加速 /
       低关注高增长），分别挑出候选——保证挑出来的插件真的会出现在对应板块。
    3. 候选的并集，先只同步"当前状态"（不算历史曲线，秒级完成）。
    4. 从"新兴增长"+"低关注高增长"这两类里额外挑一部分同步历史曲线：这两类
       插件本来就是"上线时间较短、规模还小"，最有可能在我们目前约1年的数据
       窗口内真的跨过1k/3k/10k这些里程碑档位——用它们做"快速里程碑/最近突破/
       案例研究"，才是有意义的真实数据，不是拿几个大插件硬凑
       （大插件在数据窗口开始那天可能早就超过所有档位了，
       plugin_history.compute_first_milestones 会把这种情况标成"数据开始前"，
       没法用来展示"用了多久达到"）。

item_category 字段区分插件/主题/其它类型（实测取值：extension 306306 /
theme 80013 / application 14680），只保留 extension，跟网站"Chrome Extension
数据集"的定位一致。
"""
from __future__ import annotations

import pandas as pd
from django.core.management.base import BaseCommand

from intelligence.legacy_bridge import get_metrics, get_snapshot_store
from intelligence.sync_helpers import sync_rows


class Command(BaseCommand):
    help = "一次性同步覆盖Home页各机会信号板块的一批真实插件（含少量历史曲线）。"

    def add_arguments(self, parser):
        parser.add_argument("--top-growth", type=int, default=10, help="7日增长Top N，默认10")
        parser.add_argument("--emerging", type=int, default=10, help="新兴增长挑几个，默认10")
        parser.add_argument("--accelerating", type=int, default=10, help="增长加速挑几个，默认10")
        parser.add_argument("--under-radar", type=int, default=10, help="低关注高增长挑几个，默认10")
        parser.add_argument(
            "--chart-count", type=int, default=6,
            help="额外同步历史曲线的插件数，每个约1分钟，默认6（约6分钟）",
        )

    def handle(self, *args, **options):
        snapshot_store = get_snapshot_store()
        metrics = get_metrics()

        latest = snapshot_store.get_latest_date()
        self.stdout.write(f"最新快照日期: {latest}，开始计算全量增长指标（读取9天的parquet文件，只算这一次）...")
        df = metrics.compute_metrics(base_date=latest)
        self.stdout.write(f"计算完成，共 {len(df)} 条记录（含主题/其它类型）。")

        df = df[df["item_category"] == "extension"]
        self.stdout.write(f"按 item_category == 'extension' 过滤后，共 {len(df)} 个真正的插件。")

        top_growth = df.sort_values("user_count_change_7d", ascending=False).head(options["top_growth"])

        emerging = df[
            (df["age_days"] < 365) & (df["user_count_change_30d"] > 0)
        ].sort_values("user_count_change_30d", ascending=False).head(options["emerging"])

        accelerating = df[
            df["user_count_growth_acceleration_7d"] > 0
        ].sort_values("user_count_growth_acceleration_7d", ascending=False).head(options["accelerating"])

        under_radar = df[
            (df["user_count"] < 30000) & (df["user_growth_rate_30d"] > 0.1)
        ].sort_values("user_growth_rate_30d", ascending=False).head(options["under_radar"])

        pools = {
            "7日增长Top": top_growth,
            "新兴增长": emerging,
            "增长加速": accelerating,
            "低关注高增长": under_radar,
        }
        for label, pool in pools.items():
            self.stdout.write(f"  {label}: 命中 {len(pool)} 个")

        combined = pd.concat(pools.values()).drop_duplicates(subset="id")
        self.stdout.write(f"去重后共 {len(combined)} 个插件，先同步当前状态（不算历史曲线，秒级）...")
        synced = sync_rows(combined, self.stdout, with_chart=False)
        self.stdout.write(self.style.SUCCESS(f"当前状态同步完成，共 {synced} 个插件"))

        chart_pool = pd.concat([emerging, under_radar]).drop_duplicates(subset="id")
        chart_candidates = chart_pool.sort_values("user_count", ascending=False).head(options["chart_count"])

        if len(chart_candidates) == 0:
            self.stdout.write(self.style.WARNING(
                "没有找到合适的'新兴增长/低关注高增长'候选，跳过历史曲线同步。"
                "（说明当前数据里这两类信号本身命中很少，不是命令的问题）"
            ))
            return

        self.stdout.write(
            f"开始为 {len(chart_candidates)} 个插件同步历史曲线"
            f"（现场扫描全部parquet，每个约1分钟，预计共约{len(chart_candidates)}分钟）..."
        )
        chart_synced = sync_rows(chart_candidates, self.stdout, with_chart=True)
        self.stdout.write(self.style.SUCCESS(f"历史曲线同步完成，共 {chart_synced} 个插件"))
