"""
sync_subscription_dates —— 给已存在的Stripe订阅补一次订阅周期日期。

2026-09-29新增。背景：subscription_start_date/current_period_end 这两个
字段只在webhook触发时才会写入（新订阅/续费/取消这些事件发生的时候）。如果
你的订阅是"套餐日期展示"这个功能上线之前就已经created的，Stripe不会因为
我们这边加了新字段就主动重发一次webhook——这些老订阅的日期会一直空着，
直到下个月自然续费才会被webhook补上。这个命令用来一次性同步现有订阅，
不用干等下个月。

只是一次性补数据用的工具，不是常规要跑的命令——正常情况下webhook自己会
保持这两个字段更新。

用法：
    python manage.py sync_subscription_dates
"""
from __future__ import annotations

import stripe
from django.core.management.base import BaseCommand

from intelligence.billing_views import _period_dates
from intelligence.models import UserProfile


class Command(BaseCommand):
    help = "给已存在（本功能上线前就已created）的Stripe订阅补一次订阅周期日期，一次性工具。"

    def handle(self, *args, **options):
        profiles = UserProfile.objects.exclude(stripe_subscription_id="")
        total = profiles.count()
        self.stdout.write(f"找到 {total} 个有Stripe订阅记录的账号...")

        if total == 0:
            self.stdout.write(self.style.WARNING("没有需要处理的账号。"))
            return

        updated, failed = 0, 0
        for profile in profiles:
            try:
                subscription = stripe.Subscription.retrieve(profile.stripe_subscription_id).to_dict()
            except stripe.error.StripeError as e:
                self.stdout.write(self.style.WARNING(f"  跳过 {profile.user.email}（{profile.stripe_subscription_id}）：{e}"))
                failed += 1
                continue

            profile.subscription_start_date, profile.current_period_end = _period_dates(subscription)
            profile.cancel_at_period_end = bool(subscription.get("cancel_at_period_end"))
            profile.save(update_fields=["subscription_start_date", "current_period_end", "cancel_at_period_end"])

            self.stdout.write(
                f"  已更新 {profile.user.email}: "
                f"{profile.subscription_start_date} ~ {profile.current_period_end}"
                f"{'（已标记到期取消）' if profile.cancel_at_period_end else ''}"
            )
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"完成：更新 {updated} 个，跳过 {failed} 个"))
