"""
generate_customer_dataset —— 生成真正要卖给客户的 Customer Dataset（70字段）。

跟 sync_all_extensions 是两条完全独立的管线，不要混淆：

    sync_all_extensions        只给网站/MySQL用。只保留 item_category=="extension"。

    generate_customer_dataset  真正对外销售的数据产品。包含全部 item_category
                                类型，不过滤。70个字段：25个Core Fields（现有
                                Analysis Parquet同款） + 15个新增原始产品资料
                                字段 + 29个Metrics + snapshot_date。

2026-09-28 架构确认（55→70字段扩展）：
    Analysis Parquet（现有25字段，scripts/csv_to_parquet.py 产物）—— 不动，
        继续只服务 metrics/events/rankings 等内部研究，不因为 Customer Dataset
        的需要而扩展这份parquet的字段。
    Customer Dataset Parquet（本命令新产物，70字段）—— 对外销售数据的标准底稿。
    Customer Dataset CSV（本命令新产物，70字段）—— 直接从上面那份Parquet读出来
        导出，不是另外单独拼一遍，这样保证两者Schema物理上不可能不一致。

两份Parquet字段有重复（25个Core Fields两边都有）是有意的设计，两者用途完全
不同，不强迫共用同一套Schema。

数据来自三处：
    1. metrics.compute_metrics()      → 25个Core Fields（T当天状态）+ 29个Metrics，
                                         不做 item_category 过滤（metrics.py本身
                                         就没有这个过滤逻辑，天然支持全部类型）
    2. raw_csv_reader.load_extra_fields() → 15个新增原始字段，直接读当天的
                                         raw_excel/ranking-stats-YYYYMMDD.csv
                                         （这15个字段不在Analysis Parquet里，
                                         必须读原始CSV，不能只靠parquet）
    3. snapshot_date                  → 手动加上去，即使文件名本身已经是日期，
                                         方便客户合并多天数据时知道每行属于哪天

按 id 左连接合并（以 metrics 结果的行数为准——它已经代表了"T当天实际存在的
全部产品"）。

输出目录结构：
    {CUSTOMER_DATASET_DIR}/parquet/{date}.parquet
    {CUSTOMER_DATASET_DIR}/csv/{date}.csv

用法：
    python manage.py generate_customer_dataset                    # 只生成最新一天
    python manage.py generate_customer_dataset --date 2026-08-01  # 生成指定日期
    python manage.py generate_customer_dataset --all              # 生成全部已有日期

    --all 时两种情况都会被跳过（不中断整体批处理）：
    - metrics.py 历史窗口不够（数据集刚开始那几个月）
    - 那一天的原始CSV本地已经找不到了（只剩parquet，没有raw_excel原始文件——
      70字段版本依赖原始CSV，这点跟只需要parquet就够的55字段版本不一样）
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from intelligence.legacy_bridge import get_metrics, get_snapshot_store
from intelligence.raw_csv_reader import NEW_RAW_FIELDS, load_extra_fields

CORE_FIELDS = [
    "id", "name", "item_category", "user_count", "rating_value", "rating_count",
    "version", "last_update", "creation_date", "author", "author_id", "category",
    "payment_type", "supported_languages", "num_screenshots", "num_videos",
    "is_featured", "resurrection_date", "previous_obsolete_date", "is_unlisted",
    "extension_rank", "overall_rank", "is_trusted_publisher", "by_google", "email",
]

METRIC_FIELDS = [
    "user_count_change_1d", "user_count_change_3d", "user_count_change_7d",
    "user_count_change_30d", "user_count_change_90d", "user_count_change_180d",
    "user_growth_rate_1d", "user_growth_rate_7d", "user_growth_rate_30d",
    "user_growth_rate_90d", "user_growth_rate_180d",
    "user_count_prior_7d_change", "user_count_growth_acceleration_7d",
    "user_count_prior_30d_change", "user_count_growth_acceleration_30d",
    "rating_count_change_1d", "rating_count_change_7d",
    "rating_count_change_30d", "rating_count_change_90d",
    "rating_value_change_30d", "rating_value_change_90d",
    "extension_rank_change_1d", "extension_rank_change_7d", "extension_rank_change_30d",
    "overall_rank_change_1d", "overall_rank_change_7d", "overall_rank_change_30d",
    "age_days", "days_since_update",
]

# 顺序：snapshot_date -> 25 Core Fields -> 15 新增原始字段 -> 29 Metrics
CUSTOMER_DATASET_COLUMNS = ["snapshot_date"] + CORE_FIELDS + NEW_RAW_FIELDS + METRIC_FIELDS

assert len(CORE_FIELDS) == 25, f"Core Fields 应该是25个，实际 {len(CORE_FIELDS)} 个"
assert len(NEW_RAW_FIELDS) == 15, f"新增原始字段应该是15个，实际 {len(NEW_RAW_FIELDS)} 个"
assert len(METRIC_FIELDS) == 29, f"Metrics 应该是29个，实际 {len(METRIC_FIELDS)} 个"
assert len(CUSTOMER_DATASET_COLUMNS) == 70, f"总字段数应该是70个，实际 {len(CUSTOMER_DATASET_COLUMNS)} 个"


class Command(BaseCommand):
    help = "生成对外销售的Customer Dataset（70字段），先出Parquet，再从Parquet导出同Schema的CSV。"

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default=None, help="只生成指定日期，格式 YYYY-MM-DD")
        parser.add_argument("--all", action="store_true", help="生成全部已有日期（历史窗口不够/原始CSV缺失的日期会被跳过）")

    def handle(self, *args, **options):
        if not settings.CUSTOMER_DATASET_DIR:
            raise CommandError("CUSTOMER_DATASET_DIR 没配置（依赖 LEGACY_SCRIPTS_DIR），检查 .env")

        snapshot_store = get_snapshot_store()
        metrics = get_metrics()

        if options["all"]:
            dates = snapshot_store.list_dates()
        elif options["date"]:
            dates = [options["date"]]
        else:
            dates = [snapshot_store.get_latest_date()]

        parquet_dir = Path(settings.CUSTOMER_DATASET_DIR) / "parquet"
        csv_dir = Path(settings.CUSTOMER_DATASET_DIR) / "csv"
        parquet_dir.mkdir(parents=True, exist_ok=True)
        csv_dir.mkdir(parents=True, exist_ok=True)
        self.stdout.write(f"Parquet 输出目录: {parquet_dir}")
        self.stdout.write(f"CSV 输出目录: {csv_dir}")
        self.stdout.write(f"共 {len(dates)} 个日期待处理，每份70字段（25 Core + 15新增原始字段 + 29 Metrics + snapshot_date）")

        succeeded, skipped = 0, []

        for i, date_str in enumerate(dates, start=1):
            try:
                df = metrics.compute_metrics(base_date=date_str)
            except snapshot_store.SnapshotNotFoundError as e:
                self.stdout.write(self.style.WARNING(
                    f"[{i}/{len(dates)}] 跳过 {date_str}：历史窗口不够，算不出全部Metrics（{e}）"
                ))
                skipped.append(date_str)
                continue

            try:
                extra_df = load_extra_fields(date_str)
            except FileNotFoundError as e:
                self.stdout.write(self.style.WARNING(f"[{i}/{len(dates)}] 跳过 {date_str}：{e}"))
                skipped.append(date_str)
                continue

            merged = df.merge(extra_df, on="id", how="left")

            missing = [c for c in CUSTOMER_DATASET_COLUMNS if c != "snapshot_date" and c not in merged.columns]
            if missing:
                raise CommandError(
                    f"组装出来的数据缺少这些字段，没法生成Customer Dataset: {missing}\n"
                    f"说明 metrics.py 或原始CSV的真实字段跟这条命令写死的清单不一致了，需要先核对。"
                )

            out_df = merged.copy()
            out_df.insert(0, "snapshot_date", date_str)
            out_df = out_df[CUSTOMER_DATASET_COLUMNS]

            parquet_path = parquet_dir / f"{date_str}.parquet"
            out_df.to_parquet(parquet_path, index=False, engine="pyarrow")

            # CSV 直接从刚写好的 Parquet 读回来导出——保证CSV和Parquet的Schema
            # 永远物理上一致，不会出现"改了一边忘了改另一边"的情况。
            csv_path = csv_dir / f"{date_str}.csv"
            pd.read_parquet(parquet_path, engine="pyarrow").to_csv(csv_path, index=False, na_rep="")

            self.stdout.write(f"[{i}/{len(dates)}] 已生成 {parquet_path.name} + {csv_path.name}（{len(out_df)} 行）")
            succeeded += 1

        self.stdout.write(self.style.SUCCESS(
            f"完成：成功生成 {succeeded} 个日期，跳过 {len(skipped)} 个"
        ))
        if skipped:
            self.stdout.write(f"跳过的日期: {', '.join(skipped)}")
