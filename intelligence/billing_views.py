"""
Stripe 相关的视图——单独一个文件，不跟 views.py 混在一起（跟 sitemaps.py /
sync_helpers.py 一样的组织方式，方便以后单独看这一块）。

三个入口：
- create_checkout：Pricing页点"升级"，创建一次Checkout会话，跳转到Stripe托管的付款页
- create_portal：Account页点"管理订阅"，跳转到Stripe托管的Billing Portal
- stripe_webhook：Stripe那边订阅状态变化（付款成功/取消/续费失败）时回调这里，
  这是真正更新 UserProfile.plan 的地方——不能只靠 create_checkout 那次的
  跳转就认为"订阅成功了"，必须等 Stripe 用webhook确认过一遍，否则用户中途
  关掉付款页也会被误判成升级成功。
"""
import logging
from datetime import datetime, timezone as dt_timezone

import stripe
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import billing
from .models import UserProfile

logger = logging.getLogger(__name__)


def _period_dates(subscription: dict):
    """从Stripe订阅对象（dict）里取出订阅周期的起止日期——Stripe给的是unix
    时间戳(秒)，转成date存本地，Account页面直接展示用，不用每次现查Stripe。"""
    start_ts = subscription.get("current_period_start")
    end_ts = subscription.get("current_period_end")
    start = datetime.fromtimestamp(start_ts, tz=dt_timezone.utc).date() if start_ts else None
    end = datetime.fromtimestamp(end_ts, tz=dt_timezone.utc).date() if end_ts else None
    return start, end


@login_required
@require_POST
def create_checkout(request, plan):
    if plan not in billing.PLAN_TO_PRICE:
        return HttpResponseBadRequest("Unknown plan")
    if not billing.PLAN_TO_PRICE[plan]:
        return HttpResponseBadRequest(f"No Stripe Price ID configured for {plan}")

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    success_url = request.build_absolute_uri("/account/?checkout=success")
    cancel_url = request.build_absolute_uri("/pricing/?checkout=cancelled")

    session = billing.create_checkout_session(request.user, profile, plan, success_url, cancel_url)
    return redirect(session.url)


@login_required
@require_POST
def create_portal(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    return_url = request.build_absolute_uri("/account/")
    session = billing.create_billing_portal_session(profile, return_url)
    if session is None:
        # 还没订阅过，没有Stripe客户记录，没有账单可管理，退回定价页。
        return redirect("intelligence:pricing")
    return redirect(session.url)


@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return HttpResponseBadRequest("Signature verification failed")

    event_type = event["type"]
    # 新版 stripe SDK 返回的是 StripeObject（Session/Subscription等类型），
    # 不再支持 .get() 这种字典方法（实测直接报 AttributeError，提示用
    # .to_dict()）。这里转成普通dict之后，下面 _handle_* 函数里写的
    # .get()/["key"] 才能正常用，不用把每处都改成属性访问。
    data = event["data"]["object"].to_dict()

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(data)
    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        _handle_subscription_change(data)
    elif event_type == "charge.refunded":
        _handle_charge_refunded(data)

    return HttpResponse(status=200)


def _handle_checkout_completed(session):
    """付款确认成功——把session创建时存的metadata（user_id/plan）读出来，
    真正把这个用户的方案升级上去。订阅周期日期（起始/下次扣款）另外调一次
    Stripe API 拿——Checkout Session本身不带这些字段，只有真正的Subscription
    对象才有。"""
    metadata = session.get("metadata") or {}
    user_id = metadata.get("user_id")
    plan = metadata.get("plan")
    subscription_id = session.get("subscription")
    if not user_id or not plan:
        return

    profile = UserProfile.objects.filter(user_id=user_id).first()
    if profile is None:
        return
    profile.plan = plan
    profile.stripe_subscription_id = subscription_id or ""

    if subscription_id:
        subscription = stripe.Subscription.retrieve(subscription_id).to_dict()
        profile.subscription_start_date, profile.current_period_end = _period_dates(subscription)
        profile.cancel_at_period_end = bool(subscription.get("cancel_at_period_end"))

    profile.save(update_fields=[
        "plan", "stripe_subscription_id",
        "subscription_start_date", "current_period_end", "cancel_at_period_end",
    ])


def _handle_subscription_change(subscription):
    """订阅状态变化：取消/欠费停用就降回免费版；续费/恢复正常就按订阅当时
    绑定的方案（存在subscription的metadata里，创建时就写好了）恢复，同时
    刷新订阅周期日期——每次续费Stripe都会发这个事件带最新的
    current_period_end，这样"下次扣款日期"不用额外轮询，跟着webhook自动更新。"""
    status = subscription.get("status")
    subscription_id = subscription.get("id")
    metadata = subscription.get("metadata") or {}
    user_id = metadata.get("user_id")

    profile = None
    if user_id:
        profile = UserProfile.objects.filter(user_id=user_id).first()
    if profile is None:
        profile = UserProfile.objects.filter(stripe_subscription_id=subscription_id).first()
    if profile is None:
        return

    if status in ("canceled", "unpaid", "incomplete_expired"):
        profile.plan = UserProfile.PLAN_FREE
        profile.stripe_subscription_id = ""
        profile.subscription_start_date = None
        profile.current_period_end = None
        profile.cancel_at_period_end = False
        profile.save(update_fields=[
            "plan", "stripe_subscription_id",
            "subscription_start_date", "current_period_end", "cancel_at_period_end",
        ])
    elif status == "active":
        plan = metadata.get("plan")
        if not plan:
            items = subscription.get("items", {}).get("data", [])
            if items:
                plan = billing.price_id_to_plan(items[0]["price"]["id"])
        if plan:
            profile.plan = plan
            profile.stripe_subscription_id = subscription_id
            profile.subscription_start_date, profile.current_period_end = _period_dates(subscription)
            profile.cancel_at_period_end = bool(subscription.get("cancel_at_period_end"))
            profile.save(update_fields=[
                "plan", "stripe_subscription_id",
                "subscription_start_date", "current_period_end", "cancel_at_period_end",
            ])
    elif status == "past_due":
        # 扣款失败但Stripe还在自动重试的宽限期——之前完全没处理这个状态
        # （webhook只认canceled/unpaid/incomplete_expired和active），不是
        # 故意设计的宽限逻辑，是遗漏。这里补上一个默认选择：宽限期内不降级、
        # 不清空plan/日期，让用户继续正常访问。理由：Stripe自己会按配置的
        # 重试计划反复扣款，重试成功会收到active事件自动恢复正常；重试全部
        # 失败后Stripe会发canceled/unpaid事件，上面那个分支已经会处理降级，
        # 不需要我们在past_due这一步抢先动手。
        # 这是我代为做的默认选择，不是你确认过的产品决策——如果你想要更严格
        # 的处理（比如进入past_due就先限制访问，或者在Account页面加一条
        # "支付遇到问题，请更新付款方式"的提示），告诉我再加，现在Account页面
        # 没有这个状态对应的文案。
        _, profile.current_period_end = _period_dates(subscription)
        profile.save(update_fields=["current_period_end"])
        logger.warning(
            "Stripe订阅 %s（用户id=%s）进入past_due宽限期，扣款失败，暂不降级，"
            "维持当前plan=%s，等待Stripe重试成功(active)或重试耗尽后转为canceled/unpaid",
            subscription_id, profile.user_id, profile.plan,
        )


def _handle_charge_refunded(charge):
    """退款处理（2026-09-30新增，之前完全没有——这不是遗漏是真正没做过）。

    业务规则（你明确确认过的）：
        全额退款 → 立即取消对应付费访问权限（降级Free），不等当前计费周期
                   结束——这跟"用户主动取消续费"那条路径（走
                   cancel_at_period_end，服务持续到期）刻意不同：全额退款
                   代表这笔钱已经不是我们的收入了，没有理由让访问权限继续
                   持续到"本来会用这笔钱覆盖"的那个周期结束。
        部分退款 → 不做任何自动降级动作，只记录日志——你明确要求"不要默认
                   自动降级"，部分退款的原因太多样（客服协商减免、按比例
                   退款等），不该一刀切当成"这个人不再是付费用户了"。

    charge.refunded 这个字段是Stripe自己算好的布尔值（amount_refunded >=
    amount 时为true），直接用它判断"是不是全额"，不用自己重新拿amount和
    amount_refunded相减比较——避免重复实现Stripe已经算好的逻辑，也避免
    自己实现时的浮点/货币精度问题。

    通过charge.customer（Stripe客户ID）找到对应用户，不是通过subscription——
    Charge对象不一定总带着subscription字段（比如一次性收费也会退款），
    customer字段更可靠。
    """
    customer_id = charge.get("customer")
    is_full_refund = bool(charge.get("refunded"))
    charge_id = charge.get("id")

    if not customer_id:
        logger.warning("charge.refunded事件(charge=%s)没有customer字段，无法定位用户，跳过", charge_id)
        return

    profile = UserProfile.objects.filter(stripe_customer_id=customer_id).first()
    if profile is None:
        logger.warning("charge.refunded事件(charge=%s, customer=%s)找不到对应UserProfile，跳过", charge_id, customer_id)
        return

    if not is_full_refund:
        logger.info(
            "charge=%s（用户id=%s）发生部分退款，按你的要求不自动降级，仅记录，需要人工判断是否处理",
            charge_id, profile.user_id,
        )
        return

    if profile.plan == UserProfile.PLAN_FREE:
        # 已经是免费版了（可能是更早的webhook已经处理过取消），没有可撤销的付费权限。
        return

    profile.plan = UserProfile.PLAN_FREE
    profile.stripe_subscription_id = ""
    profile.subscription_start_date = None
    profile.current_period_end = None
    profile.cancel_at_period_end = False
    profile.save(update_fields=[
        "plan", "stripe_subscription_id",
        "subscription_start_date", "current_period_end", "cancel_at_period_end",
    ])
    logger.warning(
        "charge=%s（用户id=%s）发生全额退款，已立即降级为Free（不等当前计费周期结束）",
        charge_id, profile.user_id,
    )
