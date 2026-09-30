"""
统一权限层。

真实的 Stripe 订阅对接还没做——现在 UserProfile.plan 是由后台（Django admin）
手动设置的占位状态，不是真实付费驱动的。但调用方（views.py 里的各个页面）
读取的接口就是这一个函数，以后接入真实 Stripe/订阅逻辑时，只需要改
get_viewer_plan() 内部实现（比如去查真实的订阅状态表），调用方完全不用改。
"""
from __future__ import annotations

from .models import UserProfile

PLAN_FREE = UserProfile.PLAN_FREE
PLAN_PROFESSIONAL = UserProfile.PLAN_PROFESSIONAL
PLAN_CUSTOM = UserProfile.PLAN_CUSTOM

# 免费版行数限制的占位值，具体数字未最终定（跟你之前说的一致：不要现在写死
# 太细的字段级权限），先用一个明显、好调整的数字占位。
FREE_TIER_ROW_CAP = 10


def get_viewer_plan(request) -> str:
    """已登录用户读真实 plan 字段，未登录一律按 Free 处理。"""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        profile = getattr(user, "profile", None)
        if profile is not None:
            return profile.plan
    return PLAN_FREE


def is_free(request) -> bool:
    return get_viewer_plan(request) == PLAN_FREE
