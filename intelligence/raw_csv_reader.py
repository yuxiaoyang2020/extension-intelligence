"""
raw_csv_reader.py

读取每日原始CSV（raw_excel/ranking-stats-YYYYMMDD.csv）里，Analysis Parquet
（25字段）没有收录、但 Customer Dataset 需要的那15个"产品资料"字段。

不复制、不修改原始CSV，只在生成 Customer Dataset 这一步按需读取需要的列
（pandas read_csv 的 usecols，不会把整份原始CSV——包括几十个 manifest_*
字段和权限JSON——全部加载进内存，那些字段本来就不进 Customer Dataset）。

字段名对照（2026-09-28 从真实的 raw_excel/ranking-stats-20260901.csv 表头
逐字核对过，不是猜的）：见 RAW_FIELD_MAP。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from django.conf import settings

# 原始字段名 -> Customer Dataset 里的 snake_case 字段名
RAW_FIELD_MAP = {
    "description": "description",
    "website": "website",
    "rawAuthorName": "raw_author_name",
    "publisherCountry": "publisher_country",
    "url": "url",
    "logo": "logo",
    "privacyPolicyUrl": "privacy_policy_url",
    "publisherAddress": "publisher_address",
    "helpUrl": "help_url",
    "size": "size",
    "smallBanner": "small_banner",
    "marqueeBanner": "marquee_banner",
    "isMature": "is_mature",
    "theme-rank": "theme_rank",
    "application-rank": "application_rank",
}

NEW_RAW_FIELDS = list(RAW_FIELD_MAP.values())


def _raw_csv_path(date_str: str) -> Path:
    """'2026-09-01' -> raw_excel/ranking-stats-20260901.csv（跟 csv_to_parquet.py 的命名规则完全一致）。"""
    scripts_dir = getattr(settings, "LEGACY_SCRIPTS_DIR", "") or ""
    ymd = date_str.replace("-", "")
    return Path(scripts_dir).parent / "raw_excel" / f"ranking-stats-{ymd}.csv"


def load_extra_fields(date_str: str, fields: list[str] | None = None) -> pd.DataFrame:
    """
    读取指定日期原始CSV里的 id + 新字段，重命名成 snake_case 返回。

    fields: 只读这几个snake_case字段名（比如 ["description"]），不传就是
    原来的行为——读全部15个。2026-09-30新增这个参数是因为 sync_all_extensions
    只需要 description 一个字段同步进网站，没必要跟 generate_customer_dataset
    一样把website/logo/publisher_address等14个用不到的列也一起读进内存。

    指定日期的原始CSV文件本身不存在时明确报错（调用方决定要不要兜底跳过——
    sync_all_extensions.py 网站主同步流程不能因为这个辅助字段的原始CSV缺失
    就整体失败，由它自己 try/except 处理；generate_customer_dataset.py 这边
    则是硬依赖，让异常直接抛出来是对的）。

    如果原始CSV存在、但缺少个别新字段列（比如某些历史很早的日期，chrome-stats
    还没加这几列），缺的列填NA，不报错中断——参照 csv_to_parquet.py 对25个
    Core Fields的同一套处理原则。
    """
    path = _raw_csv_path(date_str)
    if not path.exists():
        raise FileNotFoundError(
            f"找不到 {date_str} 的原始CSV文件: {path}（70字段Customer Dataset"
            f"需要原始CSV，不能只靠parquet生成）"
        )

    wanted_snake_names = fields if fields is not None else NEW_RAW_FIELDS
    wanted_raw_names = [raw for raw, snake in RAW_FIELD_MAP.items() if snake in wanted_snake_names]

    header = pd.read_csv(path, dtype=str, nrows=0).columns
    available = [c for c in wanted_raw_names if c in header]
    missing = [c for c in wanted_raw_names if c not in header]

    df = pd.read_csv(path, dtype=str, usecols=["id"] + available, low_memory=False)
    for c in missing:
        df[c] = pd.NA
    if missing:
        print(f"  [警告] {path.name} 缺少字段: {missing}，将以空值填充")

    return df.rename(columns=RAW_FIELD_MAP)
