"""
sync_top_growth —— 自动按7日增长挑一批真实插件同步，给 Home / Dataset 页面用。

用法：
    python manage.py sync_top_growth --count 10

不需要你自己去找插件id。

【只同步当前状态，不算历史曲线】Home/Dataset 页面展示的都是插件"当前状态"
（用户数、评分、7日增长），从不展示历史曲线——历史曲线只属于 Extension
Detail 页面（单个插件的在线分析），这里不需要、也不应该带上那一步慢的
现场扫描（每个插件约1分钟）。所以这条命令应该是秒级完成的，不是等几分钟。

如果你想让某个具体插件的 Detail 页面也能打开（带历史曲线），用
python manage.py sync_extension <id> 单独同步那一个。
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from intelligence.legacy_bridge import get_metrics, get_snapshot_store
from intelligence.sync_helpers import sync_rows


class Command(BaseCommand):
    help = "按7日增长Top N自动同步一批真实插件（Home/Dataset页面用）。"

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=10, help="同步几个插件，默认10个")

    def handle(self, *args, **options):
        count = options["count"]
        snapshot_store = get_snapshot_store()
        metrics = get_metrics()

        latest = snapshot_store.get_latest_date()
        self.stdout.write(f"最新快照日期: {latest}，开始计算全量增长指标（会读取9天的parquet文件）...")

        df = metrics.compute_metrics(base_date=latest)
        # 只要真正的插件，排除主题(theme)和其它类型(application)——
        # item_category 实测取值：extension 306306 / theme 80013 / application 14680。
        df = df[df["item_category"] == "extension"]
        top = df.sort_values("user_count_change_7d", ascending=False).head(count)

        self.stdout.write(f"已选出7日增长Top{count}，只同步当前状态（不算历史曲线，应该是秒级完成）...")
        synced = sync_rows(top, self.stdout, with_chart=False)
        self.stdout.write(self.style.SUCCESS(f"完成，共同步 {synced} 个插件"))
