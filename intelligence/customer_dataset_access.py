"""
Customer Dataset 下载权限——根据套餐(plan)决定这个用户能在OSS上看到/下载
哪些日期的正式历史文件。

跟 permissions.py 的分工：permissions.get_viewer_plan() 负责"这个访问者当前
是什么套餐"（登录状态、Stripe订阅状态），这里只负责"给定一个套餐，能看多少天
历史"这一条具体的产品规则，不重复判断登录/订阅状态。

Free套餐没有正式历史文件下载权限——Free拿到的是本地生成、固定50行的
Sample CSV（intelligence/raw_csv_reader.py 的兄弟模块
management/commands/generate_customer_dataset_sample.py 生成），走另一条
完全不同的路径（本地文件直接下载，不经过OSS），这里的函数不管这部分。
"""
from __future__ import annotations

import oss2
from django.conf import settings

from .models import UserProfile
from .oss_client import get_oss_bucket

# "1年内"——Professional套餐能看到的历史天数上限（2026-09-28业务规则确认，
# 2026-09-29 统一措辞：不用"近1年"这种模糊说法，明确叫"1年内"）。
PROFESSIONAL_HISTORY_DAYS = 365


def list_oss_dataset_dates() -> list[str]:
    """列出OSS上实际存在的Customer Dataset CSV日期（YYYY-MM-DD），按升序排列。"""
    bucket = get_oss_bucket()
    prefix = settings.OSS_CUSTOMER_DATASET_PREFIX.rstrip("/") + "/"
    dates = []
    for obj in oss2.ObjectIterator(bucket, prefix=prefix):
        name = obj.key[len(prefix):]
        if name.endswith(".csv"):
            dates.append(name[:-len(".csv")])
    return sorted(dates)


def get_entitled_dates(plan: str, available_dates: list[str]) -> list[str]:
    """
    给定套餐和OSS上实际存在的全部日期，返回这个套餐能下载的日期子集
    （子集，升序排列）。

    Custom：全部历史（对外说法"3年以上"）。
    Professional：最近 PROFESSIONAL_HISTORY_DAYS 天（对外说法"1年内"，按OSS
        实际存在的日期数量算，不是按自然日历——现在数据本来就不满1年，这样
        写以后数据补满了不用改代码）。
    Free / 未登录：正式历史文件一个都拿不到，只有 Sample CSV。
    """
    if plan == UserProfile.PLAN_CUSTOM:
        return list(available_dates)
    if plan == UserProfile.PLAN_PROFESSIONAL:
        return list(available_dates[-PROFESSIONAL_HISTORY_DAYS:])
    return []


def generate_download_url(date_str: str, expires_in: int = 3600) -> str:
    """生成一个有时效性的OSS签名下载链接（默认1小时过期）。"""
    bucket = get_oss_bucket()
    prefix = settings.OSS_CUSTOMER_DATASET_PREFIX.rstrip("/")
    key = f"{prefix}/{date_str}.csv"
    return bucket.sign_url("GET", key, expires_in)
