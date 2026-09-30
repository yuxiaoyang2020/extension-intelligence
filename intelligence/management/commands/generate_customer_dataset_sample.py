"""
generate_customer_dataset_sample —— 生成 Free 套餐用的 Sample CSV。

2026-09-28 业务规则确认：
    - 免费样本不能等于"某一天全部产品数据"，哪怕只给1天也不行——所以只能靠
      限制行数，不能靠"只限日期范围、行数不限"
    - 固定1个 snapshot_date、固定50行、完整70字段Schema（跟正式付费Dataset
      一模一样，不砍字段，只砍行数）
    - 50行不是简单取前50行，要尽量覆盖：不同 item_category（extension/
      theme/application）、不同 user_count 规模（大中小玩家都有）、不同
      payment_type、以及"15个新增字段"的完整度差异（有的产品资料齐全，
      有的缺东西——这样客户才能验证NULL处理是不是符合预期，不是只看到
      清一色"资料很全"的产品，产生错误预期）

抽样算法（简单、可复现，不是严格的统计学方法，够用即可）：
    1. 按 item_category 分组：非extension的类型（theme/application）各保底
       分配几行名额（如果那个类型存在的话），剩下名额全部给extension
       （因为它占绝大多数，不然50行大概率全是extension，看不出item_category
       多样性）
    2. 每组内部按 user_count 的log值分箱抽样，尽量覆盖大中小规模的产品，
       不会所有样本都是最热门的头部产品
    3. 组装完之后检查 payment_type 是否覆盖了数据里全部出现过的取值，
       没覆盖到的就换一行进来
    4. 检查15个新字段的"完整度"（非空字段数）是否有差异，样本里全部一个
       水平就换几行进来，确保完整/缺字段两种情况都能被客户看到

固定随机种子——同一个日期重复生成，结果完全一样，不会每次跑都变。

产物只存本地，不传OSS（Sample只有50行，几KB大小，直接从Django服务器发送
就行，用不上OSS那套签名下载机制，那是给几十万行的正式历史文件准备的）：
    {CUSTOMER_DATASET_DIR}/sample.csv

用法：
    python manage.py generate_customer_dataset_sample                    # 用最新一天的Customer Dataset Parquet
    python manage.py generate_customer_dataset_sample --date 2026-09-01  # 指定日期

前提：这一天的 Customer Dataset Parquet 必须已经存在（先跑过
generate_customer_dataset），这个命令不会自己去重新计算Metrics/读原始CSV。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from intelligence.management.commands.generate_customer_dataset import CUSTOMER_DATASET_COLUMNS
from intelligence.raw_csv_reader import NEW_RAW_FIELDS

SAMPLE_SIZE = 50
RANDOM_SEED = 42
MIN_PER_MINORITY_CATEGORY = 3


def _pick_diverse_rows(df: pd.DataFrame, n: int, rng: np.random.RandomState) -> pd.DataFrame:
    """在一个item_category内部，按user_count规模分箱抽n行，尽量覆盖大中小玩家。"""
    pool = df.copy()
    if len(pool) <= n:
        return pool

    user_count_numeric = pd.to_numeric(pool["user_count"], errors="coerce").fillna(0)
    log_scale = np.log10(user_count_numeric + 1)
    try:
        pool = pool.assign(_scale_bin=pd.qcut(log_scale, q=min(5, n), duplicates="drop"))
    except ValueError:
        pool = pool.assign(_scale_bin=0)

    bins = pool["_scale_bin"].unique()
    per_bin = max(1, n // len(bins))

    picked_parts = []
    for b in bins:
        bucket = pool[pool["_scale_bin"] == b]
        take = min(per_bin, len(bucket))
        picked_parts.append(bucket.sample(n=take, random_state=int(rng.randint(0, 1_000_000))))

    picked = pd.concat(picked_parts).drop(columns=["_scale_bin"])
    if len(picked) < n:
        remaining = pool.drop(picked.index, errors="ignore").drop(columns=["_scale_bin"], errors="ignore")
        extra_n = min(n - len(picked), len(remaining))
        if extra_n > 0:
            picked = pd.concat([picked, remaining.sample(n=extra_n, random_state=int(rng.randint(0, 1_000_000)))])

    return picked.head(n)


def build_sample(df: pd.DataFrame, sample_size: int = SAMPLE_SIZE, seed: int = RANDOM_SEED) -> pd.DataFrame:
    rng = np.random.RandomState(seed)

    categories = [c for c in df["item_category"].dropna().unique().tolist()]
    minority = [c for c in categories if c != "extension"]
    majority = "extension" if "extension" in categories else (categories[0] if categories else None)

    parts = []
    remaining = sample_size
    for cat in minority:
        cat_df = df[df["item_category"] == cat]
        take = min(MIN_PER_MINORITY_CATEGORY, len(cat_df), remaining)
        if take > 0:
            parts.append(_pick_diverse_rows(cat_df, take, rng))
            remaining -= take

    if majority and remaining > 0:
        maj_df = df[df["item_category"] == majority]
        parts.append(_pick_diverse_rows(maj_df, min(remaining, len(maj_df)), rng))

    sample = pd.concat(parts).drop_duplicates(subset=["id"])

    # ---- payment_type 覆盖检查 ----
    all_payment_types = set(df["payment_type"].dropna().unique())
    sample_payment_types = set(sample["payment_type"].dropna().unique())
    for pt in all_payment_types - sample_payment_types:
        candidates = df[(df["payment_type"] == pt) & (~df["id"].isin(sample["id"]))]
        if len(candidates) == 0 or len(sample) == 0:
            continue
        replacement = candidates.sample(n=1, random_state=int(rng.randint(0, 1_000_000)))
        sample = pd.concat([sample.iloc[:-1], replacement])

    # ---- 15个新字段的完整度多样性检查 ----
    completeness_all = df[NEW_RAW_FIELDS].notna().sum(axis=1)
    df_with_score = df.assign(_completeness=completeness_all)
    sample_completeness = df_with_score.loc[df_with_score["id"].isin(sample["id"]), "_completeness"]
    if len(sample) >= 2 and sample_completeness.nunique() <= 1 and len(df_with_score) > sample_size:
        low = df_with_score.nsmallest(1, "_completeness").drop(columns=["_completeness"])
        high = df_with_score.nlargest(1, "_completeness").drop(columns=["_completeness"])
        sample = pd.concat([sample.iloc[:-2], low, high]).drop_duplicates(subset=["id"])

    return sample.head(sample_size).reset_index(drop=True)


class Command(BaseCommand):
    help = "生成Free套餐的Sample CSV（固定50行、完整70字段Schema，代表性抽样，不是前50行）。"

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default=None, help="用哪一天的Customer Dataset Parquet做抽样，默认用最新一天")

    def handle(self, *args, **options):
        if not settings.CUSTOMER_DATASET_DIR:
            raise CommandError("CUSTOMER_DATASET_DIR 没配置，检查 .env")

        parquet_dir = Path(settings.CUSTOMER_DATASET_DIR) / "parquet"

        if options["date"]:
            date_str = options["date"]
        else:
            available = sorted(p.stem for p in parquet_dir.glob("*.parquet"))
            if not available:
                raise CommandError(f"{parquet_dir} 下没有任何Customer Dataset Parquet，先跑 generate_customer_dataset")
            date_str = available[-1]

        parquet_path = parquet_dir / f"{date_str}.parquet"
        if not parquet_path.exists():
            raise CommandError(f"找不到 {parquet_path}，先跑：python manage.py generate_customer_dataset --date {date_str}")

        df = pd.read_parquet(parquet_path, engine="pyarrow")
        self.stdout.write(f"从 {date_str}（{len(df)} 行）抽取 {SAMPLE_SIZE} 行代表性样本...")

        sample = build_sample(df)
        sample = sample[CUSTOMER_DATASET_COLUMNS]

        out_path = Path(settings.CUSTOMER_DATASET_DIR) / "sample.csv"
        sample.to_csv(out_path, index=False, na_rep="")

        self.stdout.write(self.style.SUCCESS(f"已生成 {out_path}（{len(sample)} 行，snapshot_date={date_str}）"))
        self.stdout.write("item_category 分布: " + str(sample["item_category"].value_counts().to_dict()))
        self.stdout.write("payment_type 分布: " + str(sample["payment_type"].value_counts().to_dict()))
