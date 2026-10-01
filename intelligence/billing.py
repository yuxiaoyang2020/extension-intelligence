"""
Stripe 订阅逻辑，集中放这一个文件——跟 legacy_bridge.py 是一样的思路，
调用方（billing_views.py）不用碰 Stripe SDK 的细节，只调这里暴露的函数。

在 Stripe 后台切到"测试模式"（左上角），用 sk_test_/pk_test_ 开头的key，
测试阶段不会真的扣款。
"""
import stripe
from django.conf import settings

stripe.api_key = settings.STRIPE_SECRET_KEY

PLAN_TO_PRICE = {
    "professional": settings.STRIPE_PRICE_PROFESSIONAL,
    "custom": settings.STRIPE_PRICE_CUSTOM,
}

_PRICE_TO_PLAN = None


def _price_to_plan_map():
    global _PRICE_TO_PLAN
    if _PRICE_TO_PLAN is None:
        _PRICE_TO_PLAN = {price: plan for plan, price in PLAN_TO_PRICE.items() if price}
    return _PRICE_TO_PLAN


def price_id_to_plan(price_id):
    return _price_to_plan_map().get(price_id)


def get_or_create_customer(user, profile):
    """确保这个用户在Stripe那边有一个Customer记录，返回customer id。
    优先复用已存的，没有才新建——避免同一个用户在Stripe后台产生多个
    重复的Customer记录。"""
    if profile.stripe_customer_id:
        return profile.stripe_customer_id
    customer = stripe.Customer.create(
        email=user.email or user.username,
        metadata={"user_id": str(user.id)},
    )
    profile.stripe_customer_id = customer.id
    profile.save(update_fields=["stripe_customer_id"])
    return customer.id


def create_checkout_session(user, profile, plan, success_url, cancel_url):
    """创建一次Stripe Checkout会话，返回session对象（调用方跳转到session.url）。"""
    price_id = PLAN_TO_PRICE.get(plan)
    if not price_id:
        raise ValueError(f"Unknown plan or no Price ID configured for it: {plan}")

    customer_id = get_or_create_customer(user, profile)
    return stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": str(user.id), "plan": plan},
        subscription_data={"metadata": {"user_id": str(user.id), "plan": plan}},
    )


def create_billing_portal_session(profile, return_url):
    """创建Billing Portal会话，让用户自己管理订阅（改绑卡、取消、看账单）。
    还没订阅过（没有stripe_customer_id）就没有账单可管理，返回None。"""
    if not profile.stripe_customer_id:
        return None
    return stripe.billing_portal.Session.create(
        customer=profile.stripe_customer_id,
        return_url=return_url,
    )
