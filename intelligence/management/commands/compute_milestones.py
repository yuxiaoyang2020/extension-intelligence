"""
compute_milestones —— 批量计算"每个插件第一次达到1K/3K/10K/30K/100K用户
是哪天"，支持 Rankings"里程碑速度"这组榜单。

2026-09-28 新增，替代原来 plugin_history.compute_first_milestones() 的
逐插件扫描方式——那个函数是给 Extension Detail 页面"点开一个插件才现场算"
设计的，一个插件就要扫近1年的parquet，30万+插件跑一遍要将近208天，完全
不现实（sync_all_extensions.py 的文档里早就说明过这个取舍，这里不重复
发明，是换一种算法专门解决"要给全部插件都算"这个不同的需求）。

算法：按天扫描（不是按插件扫描），一次遍历全部日期文件，同时给全部插件
维护"是否已经越过某个门槛"的状态；每天只读 id + user_count 两列（用
snapshot_store.load_snapshot_columns，不整份25字段都读），整个过程只需要
把每天的parquet文件读一次，而不是"每个插件重新扫一遍全部历史"。

语义跟 plugin_history.compute_first_milestones() 保持完全一致（不是另外
发明一套判断规则，只是换了更快的算法去算同一件事）：
    - "首次达到"以【这个插件在我们数据里第一次出现的那天】为起点，不是
      插件真实上线日期（真实上线日期可能比我们数据起始日期还早）
    - 如果插件第一次出现时用户数已经超过某个门槛，说明"首次达到"发生在
      我们数据范围之前，没法确定具体哪天——这一档保持NULL，不计入"最快
      达到"这个排行榜，不会瞎编一个日期
    - "用时"(days) = 首次达到门槛的日期 - 这个插件第一次出现的日期

结果写进 ExtensionMetric 新增的10个字段（days_to_X / date_to_X，X是5个
门槛），不是写 ExtensionChart.milestones——这是有意的选择：ExtensionChart
现在只有少数手动 sync_extension 同步过的插件有数据，home()首页会把
chart__isnull=False 的全部插件读进Python内存处理；如果这次也往
ExtensionChart写30万+行，会把首页那个查询从"几十行"拖成"30万行"，这个
性能影响不在这次任务范围内，所以刻意只写 ExtensionMetric（本来就是全部
插件已有的表，不会引入新的查询范围）。

前提：ExtensionMetric 里每个插件的行必须已经存在（先跑过
sync_all_extensions），这个命令只 UPDATE 新增的10个milestone字段，不会
新建整行、也不会动 user_count 等其它已有字段（_raw_bulk_upsert 的
UPDATE子句只包含传进去的字段，其它字段原样保留）。

预计耗时：约1年365个日期文件，每个文件只读2列，实测量级是几分钟到十几
分钟（不是逐插件扫描那种208天）。

用法：
    python manage.py compute_milestones
"""
from __future__ import annotations

import time

import pandas as pd
from django.core.management.base import BaseCommand
from django.utils import timezone

from intelligence.legacy_bridge import get_snapshot_store
from intelligence.models import Extension, ExtensionMetric
from intelligence.sync_helpers import _raw_bulk_upsert, _to_date

MILESTONE_THRESHOLDS = [1000, 3000, 10000, 30000, 100000]

# 第一个必须是模型里真正的字段名"extension"（不是"extension_id"，原因见
# sync_all_extensions.py 里的同一个注释——两边保持一致的原则）。
# 必须显式包含 computed_at：这个字段在数据库层面是 NOT NULL 且没有默认值
# （Django 的 auto_now=True 只在正常走 ORM 的 .save() 时才会自动填值，这里
# 是绕过 ORM 的原生SQL批量写入，不会触发那一层，不显式给值 MySQL 会直接
# 报错 "doesn't have a default value"——sync_all_extensions.py 当初就是这样
# 处理的，这里保持一致）。
METRIC_FIELDS = ["extension"] + [
    f"{prefix}_to_{t}" for t in MILESTONE_THRESHOLDS for prefix in ("days", "date")
] + ["computed_at"]


class Command(BaseCommand):
    help = "批量计算全部插件的里程碑速度（首次达到1K/3K/10K/30K/100K用户是哪天），支持Rankings里程碑组。"

    def handle(self, *args, **options):
        snapshot_store = get_snapshot_store()
        dates = snapshot_store.list_dates()
        if not dates:
            self.stdout.write(self.style.WARNING("没有找到任何快照文件。"))
            return

        thresholds = sorted(MILESTONE_THRESHOLDS)
        start_time = time.time()

        first_seen_date: dict[str, str] = {}
        reached: dict[int, dict[str, str]] = {t: {} for t in thresholds}
        already_exceeded: dict[int, set] = {t: set() for t in thresholds}

        self.stdout.write(f"共 {len(dates)} 个日期文件，开始按天扫描（只读id+user_count两列）...")

        for i, date_str in enumerate(dates, start=1):
            df = snapshot_store.load_snapshot_columns(date_str, ["id", "user_count"])
            if df is None:
                continue
            df = df.dropna(subset=["id"])
            df["user_count"] = pd.to_numeric(df["user_count"], errors="coerce")

            # 今天首次出现的插件：记录"第一次出现日期"；如果一出现用户数
            # 就已经超过某些门槛，那些门槛对这个插件直接标"数据开始前"
            # （已经在 already_exceeded 里，永远不会进 reached）。
            new_mask = ~df["id"].isin(first_seen_date.keys())
            if new_mask.any():
                new_df = df[new_mask]
                for pid, uc in zip(new_df["id"], new_df["user_count"]):
                    first_seen_date[pid] = date_str
                    if pd.notna(uc):
                        for t in thresholds:
                            if uc >= t:
                                already_exceeded[t].add(pid)

            # 每个门槛：今天有没有插件首次越过（排除已经记录过的、排除
            # "一出现就已经超过"的）——除了第一天之外，候选集合通常很小。
            for t in thresholds:
                over_mask = df["user_count"] >= t
                if not over_mask.any():
                    continue
                candidate_ids = set(df.loc[over_mask, "id"]) - reached[t].keys() - already_exceeded[t]
                for pid in candidate_ids:
                    reached[t][pid] = date_str

            if i % 30 == 0 or i == len(dates):
                elapsed = time.time() - start_time
                self.stdout.write(f"  已处理 {i}/{len(dates)} 天 · 已耗时 {elapsed:.0f}秒")

        self.stdout.write("扫描完成，开始组装结果并写入MySQL...")

        existing_ids = set(Extension.objects.values_list("extension_id", flat=True))
        self.stdout.write(f"当前MySQL里有 {len(existing_ids)} 个插件，只给这些插件写里程碑数据。")

        def days_between(reached_date: str, base_date: str) -> int:
            return (pd.to_datetime(reached_date) - pd.to_datetime(base_date)).days

        now = timezone.now()
        metric_objs = []
        skipped_no_history = 0
        for pid in existing_ids:
            if pid not in first_seen_date:
                # 这个插件在MySQL里存在（最新一天同步过），但从来没在任何
                # 历史快照里出现过——理论上不该发生，保险起见跳过，不强行
                # 拼一行全空数据。
                skipped_no_history += 1
                continue

            fields = {}
            for t in thresholds:
                if pid in reached[t]:
                    d = reached[t][pid]
                    fields[f"days_to_{t}"] = days_between(d, first_seen_date[pid])
                    fields[f"date_to_{t}"] = _to_date(d)
                else:
                    fields[f"days_to_{t}"] = None
                    fields[f"date_to_{t}"] = None
            fields["computed_at"] = now
            metric_objs.append(ExtensionMetric(extension_id=pid, **fields))

        if skipped_no_history:
            self.stdout.write(self.style.WARNING(f"跳过 {skipped_no_history} 个插件（MySQL里有，但历史快照里没找到）"))

        self.stdout.write(f"组装完成，{len(metric_objs)} 个插件，开始批量写入MySQL...")

        chunk_size = 5000
        for start in range(0, len(metric_objs), chunk_size):
            batch = metric_objs[start:start + chunk_size]
            _raw_bulk_upsert(ExtensionMetric, batch, METRIC_FIELDS)
            self.stdout.write(f"  已写入 {min(start + chunk_size, len(metric_objs))}/{len(metric_objs)}")

        elapsed = time.time() - start_time
        self.stdout.write(self.style.SUCCESS(f"完成，总耗时 {elapsed:.0f}秒"))
