import json
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe

from . import permissions
from .customer_dataset_access import generate_download_url, get_entitled_dates, list_oss_dataset_dates
from .forms import RegisterForm, ResearchRequestForm
from .legacy_bridge import get_snapshot_store
from .models import Extension, ExtensionMetric, ResearchRequest, UserProfile
from .permissions import FREE_TIER_ROW_CAP, is_free

# 不希望被搜索引擎收录的路径——跟 sitemap.py 里"只收录哪些页面"要保持一致：
# 这里禁止爬虫爬的，sitemap 里也不该出现。/admin/ 是Django自带后台，
# /staff/ 是我们自己写的内部管理页，/accounts/ /account/ 是登录/注册/账户，
# 都是私有或没有公开内容价值的页面。
ROBOTS_DISALLOW = ["/admin/", "/staff/", "/accounts/", "/account/", "/billing/", "/dataset/download/", "/healthz/"]


def health_check(request):
    """健康检查端点——给uptime监控服务（比如UptimeRobot/阿里云监控）探测用。
    做一次真实的轻量DB查询（不是只返回200不检查任何东西），MySQL连不上/
    挂了的时候这里会跟着报错，返回503，监控服务能真正探测到"服务不可用"，
    而不是"进程还活着但数据库已经死了"这种假健康状态。"""
    try:
        Extension.objects.exists()
    except Exception as exc:  # noqa: BLE001 —— 健康检查故意宽泛捕获，任何DB异常都应该报不健康
        return HttpResponse(f"unhealthy: {exc}", status=503, content_type="text/plain")
    return HttpResponse("ok", status=200, content_type="text/plain")


def robots_txt(request):
    lines = ["User-agent: *"]
    lines += [f"Disallow: {path}" for path in ROBOTS_DISALLOW]
    # 2026-09-30拆成两条独立Sitemap声明（之前只有一条/sitemap.xml）——
    # robots.txt本身支持多条Sitemap:声明，不需要额外做一个sitemap索引文件。
    lines.append(f"Sitemap: {request.build_absolute_uri('/sitemap-core.xml')}")
    lines.append(f"Sitemap: {request.build_absolute_uri('/sitemap-extension-tier1.xml')}")
    return HttpResponse("\n".join(lines), content_type="text/plain")

# 榜单类型。不是73张内部研究榜的完整移植，只挑面向付费用户好理解的几种，
# 复用的是 metrics.py 已经算好、我们已经同步进 ExtensionMetric 的字段
# （不是重新发明算法）。2026-09-28 对照 Claude Design（RANKING_CONFIG/
# RANKING_GROUPS_DEF）扩展：补了1日增长、7日增长率两个类型，新增"机会信号"
# 组——三个子类型复用 home() 首页"值得关注的机会"完全相同的筛选条件
# （opportunity_emerging/accelerating/under_radar），不是另外发明的算法。
#
# mode="opportunity" 的三个类型，row的"value"字段含义是该类型的"信号数值"
# （新兴增长→30日增长，增长加速→7日增长加速度，低关注高增长→30日增长率），
# 跟 mode="growth" 复用同一套行结构/同一套表格模板，不需要单独的表格布局。
#
# "里程碑速度"组（2026-09-28 补齐，见 compute_milestones 命令）：最快达到
# 1K/10K/100K用户——用的是 ExtensionMetric.days_to_X/date_to_X 这10个字段
# （批量向量化算法算出来的，覆盖全部同步过的插件，不是 ExtensionChart.
# milestones 那份只有少数插件有数据的旧字段）。design里是1K/10K/100K三档，
# 这里照做；3K/30K两档 compute_milestones 也算了，先不在Rankings露出。
RANKING_CONFIG = {
    "1d_growth": {"mode": "growth", "field": "user_count_change_1d", "label": "1-Day Growth", "value_label": "1-Day Growth", "is_percent": False},
    "7d_growth": {"mode": "growth", "field": "user_count_change_7d", "label": "7-Day Growth", "value_label": "7-Day Growth", "is_percent": False},
    "30d_growth": {"mode": "growth", "field": "user_count_change_30d", "label": "30-Day Growth", "value_label": "30-Day Growth", "is_percent": False},
    "7d_rate": {"mode": "growth", "field": "user_growth_rate_7d", "label": "7-Day Growth Rate", "value_label": "7-Day Growth Rate", "is_percent": True},
    "30d_rate": {"mode": "growth", "field": "user_growth_rate_30d", "label": "30-Day Growth Rate", "value_label": "30-Day Growth Rate", "is_percent": True},
    "opp_emerging": {
        "mode": "opportunity", "field": "user_count_change_30d", "label": "Emerging Growth",
        "value_label": "30-Day Growth", "is_percent": False, "subtitle": "Launched recently but already gaining users fast.",
    },
    "opp_accelerating": {
        "mode": "opportunity", "field": "user_count_growth_acceleration_7d", "label": "Accelerating Growth",
        "value_label": "7-Day Growth Acceleration", "is_percent": False, "subtitle": "Recent growth is notably faster than the prior period.",
    },
    "opp_under_radar": {
        "mode": "opportunity", "field": "user_growth_rate_30d", "label": "Under the Radar",
        "value_label": "30-Day Growth Rate", "is_percent": True, "subtitle": "Still small in scale, but growth is already standing out.",
    },
    "fast_1000": {
        "mode": "milestone", "threshold": 1000, "field": "days_to_1000",
        "label": "Fastest to 1K Users", "milestone_label": "1K", "subtitle": "Ranked by days from creation to first reaching 1,000 users.",
    },
    "fast_10000": {
        "mode": "milestone", "threshold": 10000, "field": "days_to_10000",
        "label": "Fastest to 10K Users", "milestone_label": "10K", "subtitle": "Ranked by days from creation to first reaching 10,000 users.",
    },
    "fast_100000": {
        "mode": "milestone", "threshold": 100000, "field": "days_to_100000",
        "label": "Fastest to 100K Users", "milestone_label": "100K", "subtitle": "Ranked by days from creation to first reaching 100,000 users.",
    },
}

# 分组结构——对应设计稿的 RANKING_GROUPS_DEF，页面上按组展示Segmented选择器。
RANKING_GROUPS = [
    {"label": "Growth Rankings", "items": [("1d_growth", "1D"), ("7d_growth", "7D"), ("30d_growth", "30D")]},
    {"label": "Growth Rate Rankings", "items": [("7d_rate", "7D"), ("30d_rate", "30D")]},
    {"label": "Opportunity Signals", "items": [("opp_emerging", "Emerging Growth"), ("opp_accelerating", "Accelerating Growth"), ("opp_under_radar", "Under the Radar")]},
    {"label": "Milestone Speed", "items": [("fast_1000", "1K"), ("fast_10000", "10K"), ("fast_100000", "100K")]},
]

# 机会信号组免费版行数上限比其它类型更严格（对照设计稿：机会类只给3条，
# 其它给 FREE_TIER_ROW_CAP=10 条）——机会信号是这个产品更"高价值"的洞察，
# 免费版少给一点，更能体现专业版的价值。
FREE_OPPORTUNITY_ROW_CAP = 3

# Market Explorer 的排序字段——跟 Rankings 是两回事：Rankings 是预定义榜单，
# Explorer 是用户自己筛选+排序的工作区。
EXPLORER_SORT_FIELDS = {
    "users": ("metric__user_count", "Users"),
    "7d_growth": ("metric__user_count_change_7d", "7-Day Growth"),
    "30d_growth": ("metric__user_count_change_30d", "30-Day Growth"),
    "rating": ("rating_value", "Rating"),
    "age": ("metric__age_days", "Age"),
}

# 2026-09-29 Explorer扩展（Stage 5）：数值类筛选统一用预设区间下拉（你确认
# 过"Explorer用预设档"，不是自由Min/Max输入，照抄Design mockup"全部/100+/
# 1K+/10K+/100K+"这种做法）。
#
# 每一项：(GET参数名, 显示标签, ORM字段路径, 比较方向, [(值, 展示文案), ...])
#   比较方向 "gte" = 至少达到这个值（用户数/评论数/年龄/评分/增长/增长率——
#                     越多越好的字段）
#   比较方向 "lte" = 不超过这个值（更新距今天数/排名——越小越好的字段）
# ORM字段路径要按真实模型结构写：user_count/age_days/增长相关字段在
# ExtensionMetric上（要带 metric__ 前缀）；rating_value/rating_count/
# extension_rank/overall_rank 直接在Extension上（不用前缀）。
EXPLORER_NUMERIC_FILTERS = [
    ("min_users", "Min Users", "metric__user_count", "gte",
     [("100", "100+"), ("1000", "1K+"), ("10000", "10K+"), ("100000", "100K+")]),
    ("min_rating", "Min Rating", "rating_value", "gte",
     [("4", "4.0+"), ("4.5", "4.5+")]),
    ("min_reviews", "Min Reviews", "rating_count", "gte",
     [("10", "10+"), ("100", "100+"), ("1000", "1K+"), ("10000", "10K+")]),
    ("min_age", "Extension Age (min)", "metric__age_days", "gte",
     [("30", "30d+"), ("180", "180d+"), ("365", "1yr+"), ("730", "2yr+")]),
    ("max_days_since_update", "Last Updated Within", "metric__days_since_update", "lte",
     [("7", "7 days"), ("30", "30 days"), ("90", "90 days")]),
    ("min_g1d", "1-Day Growth (min)", "metric__user_count_change_1d", "gte",
     [("0", "Positive growth"), ("100", "100+"), ("1000", "1K+")]),
    ("min_g7d", "7-Day Growth (min)", "metric__user_count_change_7d", "gte",
     [("0", "Positive growth"), ("500", "500+"), ("5000", "5K+")]),
    ("min_g30d", "30-Day Growth (min)", "metric__user_count_change_30d", "gte",
     [("0", "Positive growth"), ("1000", "1K+"), ("10000", "10K+")]),
    ("min_rate7d", "7-Day Growth Rate (min)", "metric__user_growth_rate_7d", "gte",
     [("0", "Positive growth"), ("0.1", "10%+"), ("0.3", "30%+")]),
    ("min_rate30d", "30-Day Growth Rate (min)", "metric__user_growth_rate_30d", "gte",
     [("0", "Positive growth"), ("0.1", "10%+"), ("0.5", "50%+")]),
    ("max_ext_rank", "Extension Rank", "extension_rank", "lte",
     [("100", "Top 100"), ("1000", "Top 1,000"), ("10000", "Top 10K"), ("100000", "Top 100K")]),
    ("max_overall_rank", "Overall Rank", "overall_rank", "lte",
     [("100", "Top 100"), ("1000", "Top 1,000"), ("10000", "Top 10K"), ("100000", "Top 100K")]),
]

# 三个勾选筛选——原始数据是字符串"True"/"False"（见Extension.is_featured等
# 字段注释），这里筛选按Python bool比较，"全部/是/否"三态下拉。
EXPLORER_BOOL_FILTERS = [
    ("featured", "Featured", "is_featured"),
    ("trusted", "Trusted Publisher", "is_trusted_publisher"),
    ("by_google", "By Google", "by_google"),
]

# 机会标签——直接复用Rankings"机会信号"组完全相同的筛选条件
# （_opportunity_queryset()），不是另外发明的算法。
EXPLORER_OPPORTUNITY_OPTIONS = [
    ("opp_emerging", "Emerging Growth"),
    ("opp_accelerating", "Accelerating Growth"),
    ("opp_under_radar", "Under the Radar"),
]

# 当前 parquet 里真实存在的25个字段，按用途分组，仅供 Dataset 页面展示用。
# 跟 snapshot_store.EXPECTED_COLUMNS 保持一致——这里没有 import 那个列表，
# 是因为这是"展示分组"，属于页面文案范畴，不是数据校验逻辑，两者职责不同。
FIELD_SCHEMA = [
    {"group": "Extension Info", "fields": ["id", "name", "category", "item_category", "author", "author_id", "email"]},
    {"group": "Store Data", "fields": ["version", "creation_date", "last_update", "payment_type",
                                   "supported_languages", "num_screenshots", "num_videos"]},
    {"group": "Status Flags", "fields": ["is_featured", "is_trusted_publisher", "by_google", "is_unlisted",
                                   "resurrection_date", "previous_obsolete_date"]},
    {"group": "Growth & Ranking", "fields": ["user_count", "rating_value", "rating_count", "extension_rank", "overall_rank"]},
]

# Account页面"套餐包含"区块用——文案照抄Pricing页每档方案下面的功能列表，
# 保持两处说法一致。这里没有跟pricing.html共享同一份数据结构（那边是直接
# 写死在模板里的），以后要改哪档的功能描述，两处都要记得改，这是当前规模
# 下可以接受的重复（不是很多地方引用，改的频率也不高）。
# 2026-09-29：套餐日期范围统一用"1年内"（Professional）/"3年以上"（Custom）
# 这两个精确说法，不再用"近1年"这种模糊表述——同样的措辞也要跟Pricing页
# 保持一致（那边是写死在模板里的，没有共享这份数据）。
PLAN_FEATURES = {
    "free": ["Search extensions", "Basic extension info", "Rankings preview (limited results)", "Opportunity signal preview (Top 3)", "30-day history analysis", "Sample dataset data"],
    "professional": ["Full rankings and opportunity boards", "Advanced Market Explorer filters", "Full extension history analysis", "Peer comparisons and milestone analysis", "Chrome Extension Dataset access (1-year history)", "Historical dataset export"],
    "custom": ["Everything in Professional", "Full historical dataset access (3+ years)", "Custom market and keyword research", "Chrome Web Store keyword ranking research", "Competitor and localization research", "Custom CSV / Excel reports"],
}

MILESTONE_THRESHOLDS = [1000, 3000, 10000, 30000, 100000]

# 计算"增长率"类信号（30日增长率）时，要求30天前的基数至少这么大——
# metrics.py 的 user_growth_rate_30d = (current-past)/past，past 接近0时
# 哪怕只涨了几十个用户，比率也会被放大到几千%（实测出现过999900%这种
# 明显失真的数字），不是真实的"高增长"信号，是除以接近0的数学假象。
# 这个下限只影响"按增长率排序/筛选"的场景，不影响 sync 时原样存进
# MySQL 的真实 user_growth_rate_30d 数值。
MIN_BASE_FOR_GROWTH_RATE = 100


def _sparkline_path(points, value_key, width=740, height=230, x0=50, pad_top=20, pad_bottom=20):
    """把历史数据点画成一条 SVG <path> 的 d 属性——纯几何映射（取值范围内线性
    缩放到 viewBox），不做平滑/插值，忠实反映原始数据点。"""
    values = [p.get(value_key) for p in points if p.get(value_key) is not None]
    if not values:
        return ""
    vmin, vmax = min(values), max(values)
    vrange = (vmax - vmin) or 1
    n = len(points)
    coords = []
    for i, p in enumerate(points):
        v = p.get(value_key)
        if v is None:
            continue
        x = x0 + (width - x0) * i / max(n - 1, 1)
        y = pad_top + (height - pad_top - pad_bottom) * (1 - (v - vmin) / vrange)
        coords.append(f"{x:.1f},{y:.1f}")
    return "M " + " L ".join(coords) if coords else ""


def _build_demo_case_study():
    """
    首页"案例研究"/"不只是看到今天"用的虚构示例数据——固定随机种子，每次
    访问看到的曲线都一样（不是每次刷新随机跳动），明确标注是示例、不对应
    任何真实插件（见 home() 里的说明）。

    曲线生成方式：从一个很小的起始值开始，每天按一个随机但总体向上的比例
    增长，偶尔插入几天近乎停滞的"平台期"，做出一条真实增长曲线常见的
    "总体向上、有起伏"的样子，不是一条假的平滑直线。里程碑用同一条曲线
    反推"第一次达到某个用户量是哪天"，保证曲线和里程碑表格的数字是自洽的，
    不是分开编的两组数字。
    """
    import random
    from datetime import date, timedelta

    rng = random.Random(20260927)
    start = date(2025, 9, 1)
    total_days = 365

    history = []
    value = 40.0
    for i in range(total_days):
        growth = value * rng.uniform(0.006, 0.032)
        if rng.random() < 0.12:
            growth *= 0.1
        value += growth
        history.append({"date": (start + timedelta(days=i)).isoformat(), "user_count": round(value)})

    milestones = []
    for m in MILESTONE_THRESHOLDS:
        hit = next((p for p in history if p["user_count"] >= m), None)
        if hit:
            days_since = (date.fromisoformat(hit["date"]) - start).days
            milestones.append({"milestone": m, "first_reached_date": hit["date"], "days_since_launch": days_since})

    return {
        "name": "Sample Extension (demo data, not a real extension)",
        "category": "productivity/tools",
        "history": history,
        "milestones": milestones,
    }


def _valid_milestones(chart):
    """从 ExtensionChart.milestones 里挑出"真的有首次达成日期"的条目
    （排除 first_reached_date 为 None 或 "数据开始前" 的）。"""
    if not chart:
        return []
    out = []
    for m in chart.milestones or []:
        if m.get("first_reached_date") and m.get("days_since_launch") is not None:
            out.append(m)
    return out


def home(request):
    """
    Home 页面，对齐设计稿的模块结构：
    Hero+搜索 -> Dataset模块(紧跟Hero) -> 7日增长预览 -> 值得关注的机会(4个)
    -> 最近突破 -> 不只是看到今天 -> 案例研究

    数据现状（如实说明，不是简化借口）：
    - "值得关注的机会"里的"新兴增长/增长加速/低关注高增长"三个信号，只需要
      ExtensionMetric 的字段，已同步的插件都能用。
    - "快速里程碑"、"最近突破"、"不只是看到今天"、"案例研究"这4个依赖历史曲线
      +里程碑数据（ExtensionChart），目前只有手动 sync_extension 同步过的插件
      才有这份数据，样本可能很少（甚至只有1个），不是bug，是当前数据覆盖范围
      的真实情况。
    """
    # 2026-09-30：你明确要求直接写死"3 year+"，不要再动态算——历史回填
    # 已经是真实存在的数据（2023-09-01至今），不需要看哪台机器当下同步了
    # 多少再现算这个数字。以后数据范围变化需要改这个值时，直接改这一行。
    history_years_label = "3 year+"

    query = request.GET.get("q", "").strip()
    # 2026-09-30修复：搜索框提示文字写的是"搜索插件名称或Extension ID"，但
    # 之前的代码只按name模糊匹配，完全没处理Extension ID——输入一个真实的
    # Extension ID，只会拿去跟name字段做字符串匹配，永远搜不到，UI承诺的
    # 功能和代码实际做的对不上。extension_id用iexact（精确匹配，大小写不
    # 敏感）而不是icontains：ID是精确标识符不需要模糊匹配，而且iexact能用上
    # 主键索引，在26万+行规模下比对PK做LIKE '%...%'全表扫描快得多。
    search_results = (
        Extension.objects.filter(Q(name__icontains=query) | Q(extension_id__iexact=query))[:10]
        if query else []
    )

    with_metric = Extension.objects.select_related("metric").filter(metric__isnull=False)

    top_growth = with_metric.order_by("-metric__user_count_change_7d")[:8]

    # 新兴增长：上线时间较短，且30日仍在增长
    opportunity_emerging = with_metric.filter(
        metric__age_days__lt=365, metric__user_count_change_30d__gt=0
    ).order_by("-metric__user_count_change_30d")[:5]

    # 增长加速：metrics.py 原生算出的7日加速度>0（最近7日比前7日涨得快）
    opportunity_accelerating = with_metric.filter(
        metric__user_count_growth_acceleration_7d__gt=0
    ).order_by("-metric__user_count_growth_acceleration_7d")[:5]

    # 低关注高增长：当前用户规模还不大，但30日增长率明显。
    # 额外要求30天前的基数(=当前-30日变化)至少达到 MIN_BASE_FOR_GROWTH_RATE，
    # 排除"从几乎0涨到一点点也能算出几千%"这种除以接近0的假信号。
    opportunity_under_radar = with_metric.annotate(
        past_user_count_30d=F("metric__user_count") - F("metric__user_count_change_30d")
    ).filter(
        metric__user_count__lt=30000,
        metric__user_growth_rate_30d__gt=0.1,
        past_user_count_30d__gte=MIN_BASE_FOR_GROWTH_RATE,
    ).order_by("-metric__user_growth_rate_30d")[:5]

    # 快速里程碑 / 最近突破 / 历史教程 / 案例研究：都依赖 ExtensionChart
    with_chart = list(Extension.objects.select_related("chart", "metric").filter(chart__isnull=False))

    fast_milestones = []
    for ext in with_chart:
        valid = _valid_milestones(ext.chart)
        if valid:
            fastest = min(valid, key=lambda m: m["days_since_launch"])
            fast_milestones.append({"extension": ext, "milestone": fastest})
    fast_milestones.sort(key=lambda item: item["milestone"]["days_since_launch"])
    opportunity_fast_milestone = fast_milestones[:5]

    recent_milestones = []
    for ext in with_chart:
        for m in _valid_milestones(ext.chart):
            recent_milestones.append({"extension": ext, "milestone": m})
    recent_milestones.sort(key=lambda item: item["milestone"]["first_reached_date"], reverse=True)
    recent_milestones = recent_milestones[:8]

    # "案例研究"和"不只是看到今天"这两个模块用的是虚构示例数据，不对应任何
    # 真实插件——这是产品决定（2026-09-27确认）：这两个模块的目的是营销展示
    # "我们的历史数据能讲出什么样的故事"，如果挂在一个真实、具名的插件（比如
    # 之前试过的Adobe Acrobat）头上展示编造的增长曲线，等于对外发布关于一个
    # 真实公司产品的虚假业绩数据，这个风险不能接受。
    #
    # 明确的边界：这个虚构只用在首页这两个营销模块，绝不影响 Extension Detail
    # 页——那个页面永远只显示真实同步数据（见 extension_detail() 和它的
    # "暂无历史曲线数据"空状态），这两个模块的按钮也不会链接到任何插件详情页
    # （链到 Dataset / Pricing，见 home.html），不会让人误以为点进去能看到
    # 这个"示例插件"的真实详情。
    demo = _build_demo_case_study()
    case_study = {
        "name": demo["name"],
        "category": demo["category"],
        "milestones": demo["milestones"],
        "chart_path": _sparkline_path(demo["history"], "user_count", width=660, height=150),
    }
    history_teaser = {
        "first_point": demo["history"][0],
        "last_point": demo["history"][-1],
    }

    from .testimonials_content import get_testimonials

    context = {
        "history_years_label": history_years_label,
        "query": query,
        "search_results": search_results,
        "top_growth": top_growth,
        "opportunity_emerging": opportunity_emerging,
        "opportunity_accelerating": opportunity_accelerating,
        "opportunity_under_radar": opportunity_under_radar,
        "opportunity_fast_milestone": opportunity_fast_milestone,
        "recent_milestones": recent_milestones,
        "case_study": case_study,
        "history_teaser": history_teaser,
        # 首发要求：没有真实客户评价前，生产环境不展示虚假Testimonials——
        # 列表现在是空的（见testimonials_content.py），home.html靠
        # {% if testimonials %}让这个区块在没有真实内容时完全不渲染。
        "testimonials": get_testimonials(),
        "meta_title": "Extension Intelligence — Chrome Extension Market Intelligence",
        "meta_description": "Discover growing Chrome extensions and explore historical data and growth trends.",
        "page_type": "seo_landing",
    }
    return render(request, "intelligence/home.html", context)


def dataset(request):
    """
    Dataset 页面——数据产品的主要浏览/下载入口（不是Account页面）。

    字段预览（"示例数据预览"表格）继续用当前25字段里的几个代表性字段做轻量
    展示，为了页面可读性不需要把真实产品的70列都横向摆出来——这只是"这个
    数据集长什么样"的预览，跟"Sample CSV下载"是两回事：Sample CSV是真实
    Customer Dataset Pipeline产出的完整70字段文件（只是固定50行），预览表
    只是给不下载文件的访客一个直观印象。

    正式历史文件下载区：根据 permissions.get_viewer_plan() 判断套餐，
    Professional/Custom 已登录用户能看到自己权限范围内的可下载日期列表
    （从OSS现查，生成的是有时效性的签名链接，见 dataset_download）；
    Free/未登录只能看到"下载Sample CSV"+"升级解锁完整历史"。
    """
    snapshot_store = get_snapshot_store()
    dates = snapshot_store.list_dates()
    latest = dates[-1] if dates else None

    preview_columns = ["id", "name", "category", "user_count", "rating_value", "rating_count", "version", "last_update"]
    sample_rows = []
    if latest:
        df = snapshot_store.load_snapshot(latest)
        # 排除主题(theme)和其它类型(application)，只展示真正的插件——这是
        # 直接读原始parquet的示例预览，不经过MySQL，所以要在这里单独过滤，
        # 跟 sync_* 命令那边的过滤是两处独立但一致的逻辑。这只影响页面上的
        # 预览表格，不影响Sample CSV（那个包含全部item_category类型）。
        df = df[df["item_category"] == "extension"]
        # 2026-09-29 缩短到5行——这只是"长什么样"的示意，不是数据浏览器，
        # 20行对预览来说太长了。
        sample_rows = df[preview_columns].head(5).to_dict("records")

    viewer_plan = permissions.get_viewer_plan(request)
    entitled_dates = []
    downloads_unavailable = False
    if request.user.is_authenticated and viewer_plan in (UserProfile.PLAN_PROFESSIONAL, UserProfile.PLAN_CUSTOM):
        try:
            oss_dates = list_oss_dataset_dates()
            entitled_dates = list(reversed(get_entitled_dates(viewer_plan, oss_dates)))
        except Exception:
            # OSS没配置好/网络问题——不让整个Dataset页面挂掉，只是这一块暂时
            # 显示"暂不可用"，其它内容（预览、Sample下载）照常展示。
            downloads_unavailable = True

    # 2026-09-30三次修正：顶部统计卡片的"覆盖时长"和"Earliest date"你要求
    # 直接写死，不要再从这台机器本地`parquet/`目录现算——跟首页"3 year+"
    # 是同一件事：这台机器本地文件不全不代表平台真实数据范围只有这么多，
    # 写死跟首页说法保持一致。注意这里特意没有复用`total_dates`这个变量名
    # 去装写死的"3+ years"字符串——下面"Historical snapshot files (most
    # recent 10 of X)"那句话里的X必须是这台机器本地真实的文件数量（不是
    # 营销话术，是"你能在下面这个列表里翻到多少条"这个功能性描述，写死会
    # 让那句话变得前后矛盾），所以拆成两个独立变量，不共用一个。
    history_coverage_label = "3+ years"
    earliest_year = "2023"

    context = {
        "history_coverage_label": history_coverage_label,
        "total_dates": len(dates),
        "earliest_date": earliest_year,
        "latest_date": "Today" if dates else None,
        "recent_dates": list(reversed(dates))[:10],
        "field_schema": FIELD_SCHEMA,
        "sample_date": latest,
        "sample_rows": sample_rows,
        "sample_hidden_field_count": 70 - len(preview_columns),
        "viewer_plan": viewer_plan,
        "entitled_dates": entitled_dates,
        "downloads_unavailable": downloads_unavailable,
        "meta_title": "Chrome Extension Dataset — Full Historical Dataset | Extension Intelligence",
        "meta_description": "Daily historical data for 300K+ Chrome extensions, standardized fields, CSV export.",
        "page_type": "dataset",
    }
    return render(request, "intelligence/dataset.html", context)


def dataset_sample_csv(request):
    """
    Free套餐的Sample CSV——固定50行、完整70字段Schema，跟付费版一模一样的
    Schema，只是行数被限制（业务规则：哪怕只给1天的数据，只要不限行数就等于
    白送一份完整市场快照，所以必须限制行数，不能只限日期范围）。

    由 generate_customer_dataset_sample 命令预先生成好放在本地磁盘——文件
    只有50行、几KB大小，直接从Django发送即可，不需要走OSS那套签名下载
    （那是给几十万行的正式历史文件准备的）。
    """
    if not settings.CUSTOMER_DATASET_DIR:
        return HttpResponse("Sample dataset is not configured yet.", status=503)

    sample_path = Path(settings.CUSTOMER_DATASET_DIR) / "sample.csv"
    if not sample_path.exists():
        return HttpResponse(
            "Sample dataset has not been generated yet — please ask an admin to run python manage.py generate_customer_dataset_sample.",
            status=503,
        )

    response = FileResponse(open(sample_path, "rb"), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="customer_dataset_sample.csv"'
    return response


@login_required
def dataset_download(request, date_str):
    """
    正式历史文件下载——校验这个日期是否在当前用户套餐的权限范围内，通过才
    生成一个有时效性的OSS签名链接并跳转过去（不是把文件代理下载一遍，几十万
    行的CSV不应该经过我们自己的服务器）。
    """
    viewer_plan = permissions.get_viewer_plan(request)

    try:
        oss_dates = list_oss_dataset_dates()
    except Exception:
        messages.error(request, "Download service is temporarily unavailable, please try again later.")
        return redirect("intelligence:dataset")

    entitled = get_entitled_dates(viewer_plan, oss_dates)
    if date_str not in entitled:
        messages.error(request, "Your current plan doesn't include download access for this date.")
        return redirect("intelligence:pricing")

    return redirect(generate_download_url(date_str))


def _opportunity_queryset(base_qs, ranking_type):
    """
    机会信号组的筛选条件——跟 home() 首页"值得关注的机会"三个卡片完全
    相同的条件，直接复用，不是另外发明的算法（见 home() 里
    opportunity_emerging/opportunity_accelerating/opportunity_under_radar
    三段注释里的解释）。
    """
    if ranking_type == "opp_emerging":
        return base_qs.filter(
            metric__age_days__lt=365, metric__user_count_change_30d__gt=0
        ).order_by("-metric__user_count_change_30d")
    if ranking_type == "opp_accelerating":
        return base_qs.filter(
            metric__user_count_growth_acceleration_7d__gt=0
        ).order_by("-metric__user_count_growth_acceleration_7d")
    # opp_under_radar
    return base_qs.annotate(
        past_user_count_30d=F("metric__user_count") - F("metric__user_count_change_30d")
    ).filter(
        metric__user_count__lt=30000,
        metric__user_growth_rate_30d__gt=0.1,
        past_user_count_30d__gte=MIN_BASE_FOR_GROWTH_RATE,
    ).order_by("-metric__user_growth_rate_30d")


def rankings(request):
    """
    Rankings 页面。2026-09-28 对照 Claude Design 扩展：增长排行(1/7/30日) +
    增长率排行(7/30日) + 机会信号(新兴增长/增长加速/低关注高增长) + 里程碑
    速度(1K/10K/100K)，共11种类型、4个分组。

    mode="growth" 和 mode="opportunity" 复用同一套行结构/表格模板——两者
    本质上都是"取一批插件 + 按某个数值字段排序展示"，只是构造queryset的筛选
    条件不同，没必要做成两套模板。mode="milestone" 是升序排序（天数越少
    排名越靠前），而且展示的列不一样（里程碑/用时/达成日期，不是单个
    "value"），单独一套行结构。

    完整结果支持分页（每页30条）；免费版行数上限"机会信号"组比其它组更严格
    （3条 vs 10条），对照设计稿的做法。
    """
    ranking_type = request.GET.get("type", "7d_growth")
    if ranking_type not in RANKING_CONFIG:
        ranking_type = "7d_growth"
    config = RANKING_CONFIG[ranking_type]
    field = config["field"]
    mode = config["mode"]

    base_qs = (
        Extension.objects.select_related("metric")
        .filter(metric__isnull=False)
        .exclude(**{f"metric__{field}__isnull": True})
    )

    if mode == "opportunity":
        queryset = _opportunity_queryset(base_qs, ranking_type)
        cap = FREE_OPPORTUNITY_ROW_CAP
    elif mode == "milestone":
        # 天数越少代表越快达到，升序排序（跟growth/opportunity的降序相反）。
        queryset = base_qs.order_by(f"metric__{field}")
        cap = FREE_TIER_ROW_CAP
    else:
        queryset = base_qs
        if config["is_percent"]:
            # 增长率类型：7日/30日基数太小时，增长率会被除法放大到失真
            # （比如999900%），这里排除掉，不是真实的高增长——跟首页
            # "低关注高增长"同样的道理，只是这里对7日/30日通用处理。
            period = ranking_type.split("_")[0]  # "7d" 或 "30d"
            queryset = queryset.annotate(
                past_user_count=F("metric__user_count") - F(f"metric__user_count_change_{period}")
            ).filter(past_user_count__gte=MIN_BASE_FOR_GROWTH_RATE)
        queryset = queryset.order_by(f"-metric__{field}")
        cap = FREE_TIER_ROW_CAP

    total_count = queryset.count()
    free_user = is_free(request)
    locked = free_user and total_count > cap
    capped_total = min(total_count, cap) if locked else total_count

    # 2026-09-30修复真实性能bug（你反馈"加载速度肯定不行"排查出来的）：
    # 这里原来是`list(queryset[:cap]) if locked else list(queryset)`——
    # `locked`只有"免费用户且超过行数上限"才为True，付费用户/结果数没超过
    # 免费上限时走的是`list(queryset)`，也就是把整个匹配结果（可能几万到
    # 几十万行Extension+ExtensionMetric）**全部**拉进Python内存、构造成
    # 完整ORM对象，然后才用Paginator在内存里切出当前页这30条——分页形同
    # 虚设，实际上每次翻页/换榜单类型都要重新查询+构造全部匹配行。改成
    # 用Paginator(range(capped_total), 30)只计算分页元数据（页码/上一页/
    # 下一页，不触碰真实数据，range对象天然O(1)），算出当前页对应的
    # offset后，直接在QuerySet上切片`queryset[offset:offset+30]`——这才是
    # 真正的SQL LIMIT/OFFSET，每次请求只从MySQL取30行，不管总共匹配多少万行。
    pager = Paginator(range(capped_total), 30)
    page_obj = pager.get_page(request.GET.get("page", 1))
    offset = (page_obj.number - 1) * 30
    page_queryset = queryset[offset:offset + 30]

    if mode == "milestone":
        date_field = f"date_to_{config['threshold']}"
        rows = [
            {
                "rank": offset + i + 1, "extension": ext,
                "days": getattr(ext.metric, field),
                "reached_date": getattr(ext.metric, date_field),
            }
            for i, ext in enumerate(page_queryset)
        ]
    else:
        rows = [
            {"rank": offset + i + 1, "extension": ext, "value": getattr(ext.metric, field)}
            for i, ext in enumerate(page_queryset)
        ]

    ranking_groups = [
        {
            "label": grp["label"],
            "items": [
                {"key": key, "label": item_label, "active": key == ranking_type}
                for key, item_label in grp["items"]
            ],
        }
        for grp in RANKING_GROUPS
    ]

    context = {
        "ranking_groups": ranking_groups,
        "current_type": ranking_type,
        "current_config": config,
        "page_obj": page_obj,
        "rows": rows,
        "locked": locked,
        "total_count": total_count,
        # 2026-09-30确认的Rankings SEO规则：只有真正"裸"的/rankings/（没有
        # type/page这些query参数）才index；带着任何参数的状态页——不管是
        # 换了榜单类型还是翻了页——一律noindex,follow。以后哪个榜单类型真的
        # 值得单独收录，会给它做一条独立干净的URL（比如/rankings/fastest-
        # growing/），不是继续加宽这条query parameter的index范围。
        "should_index": "type" not in request.GET and "page" not in request.GET,
        "meta_title": f"{config['label']} Rankings — Extension Intelligence",
        "meta_description": f"See the Chrome extension {config['label']} rankings, covering {total_count} extensions.",
        "page_type": "seo_landing",
    }
    return render(request, "intelligence/rankings.html", context)


def explorer(request):
    """
    Market Explorer 页面——用户自己按条件筛选，跟 Rankings 的"预定义榜单"是
    两种不同的使用意图，不应该做成同一个页面。

    分类/商业模式的下拉选项从已同步的真实数据里动态取值，不写死一份列表——
    以后同步了更多插件，这里自动会有更多可选项，不用改代码。

    2026-09-29 Explorer扩展（Stage 5）：数值类筛选全部改成预设区间下拉
    （EXPLORER_NUMERIC_FILTERS，你确认过用预设档，不是自由Min/Max输入），
    新增Featured/Trusted Publisher/By Google三个勾选筛选（依赖
    sync_all_extensions.py 这次一起加的三个新字段，需要你重新跑一次全量
    同步），新增机会标签筛选（复用Rankings"机会信号"组完全相同的筛选条件，
    不是另外发明的算法）。

    免费版行数上限复用 permissions.py 的占位逻辑；这些新筛选对免费用户
    也都正常开放，跟之前"商业模式筛选不算高级功能"是同一个态度，不因为
    这次扩展就突然收紧免费版权限。
    """
    category = request.GET.get("category", "")
    payment_type = request.GET.get("payment_type", "")
    opportunity = request.GET.get("opportunity", "")
    sort_key = request.GET.get("sort", "30d_growth")
    if sort_key not in EXPLORER_SORT_FIELDS:
        sort_key = "30d_growth"
    sort_dir = request.GET.get("dir", "desc")

    queryset = Extension.objects.select_related("metric").filter(metric__isnull=False)

    # 2026-09-30改了两次：第一次是"下拉+旁边独立数字输入框"两个并排控件，
    # 你反馈"都选是什么意思，太尴尬了"；第二次改成单个<input>配HTML原生
    # <datalist>，结果不同浏览器对datalist建议列表显示value还是label不
    # 一致，有的浏览器直接显示裸数字（比如"365"），脱离上下文看不懂——这是
    # datalist这个原生控件的已知局限，不是属性没写对。最终方案：只用一个
    # 真正的<input type="number">接收值（后端逻辑完全不用变，还是收
    # 一个数字），预设选项做成模板里的按钮（chip），点击后用JS把值填进
    # 这个输入框——按钮上的文字是我们模板里直接写的{{ label }}，不依赖
    # 任何浏览器原生控件的渲染行为，100%可控。
    numeric_values = {}
    for param, label, field_path, direction, options in EXPLORER_NUMERIC_FILTERS:
        value = request.GET.get(param, "").strip()
        numeric_values[param] = value
        if value:
            try:
                queryset = queryset.filter(**{f"{field_path}__{direction}": float(value)})
            except ValueError:
                # 手动改URL传了非法值，或者输入框填了非数字——忽略这个筛选
                # 条件，不让整个页面500。
                numeric_values[param] = ""

    bool_values = {}
    for param, label, field_name in EXPLORER_BOOL_FILTERS:
        value = request.GET.get(param, "")
        bool_values[param] = value if value in ("yes", "no") else ""
        if bool_values[param] == "yes":
            queryset = queryset.filter(**{field_name: True})
        elif bool_values[param] == "no":
            queryset = queryset.filter(**{field_name: False})

    if category:
        queryset = queryset.filter(category=category)
    if payment_type:
        queryset = queryset.filter(payment_type=payment_type)
    if opportunity not in dict(EXPLORER_OPPORTUNITY_OPTIONS):
        opportunity = ""
    elif opportunity:
        queryset = _opportunity_queryset(queryset, opportunity)

    order_field, _ = EXPLORER_SORT_FIELDS[sort_key]
    queryset = queryset.order_by(order_field if sort_dir == "asc" else f"-{order_field}")

    total_count = queryset.count()
    free_user = is_free(request)
    locked = free_user and total_count > FREE_TIER_ROW_CAP

    # 2026-09-30修复真实性能bug（同Rankings页那处，你反馈"加载速度肯定
    # 不行"排查出来的）：原来是`list(queryset[:FREE_TIER_ROW_CAP]) if locked
    # else list(queryset)`——没被锁定时（付费用户，或免费用户但结果数没超过
    # 上限）会把全部匹配结果拉进Python内存构造成ORM对象，Paginator只是在
    # 内存里切当前页，分页起不到限制单次查询数据量的作用。这里不需要像
    # Rankings那样额外算rank，直接把QuerySet（懒加载，可选先截断到
    # FREE_TIER_ROW_CAP）原样交给Paginator——Django的Paginator对QuerySet
    # 分页时，只有真正遍历某一页的object_list时才会执行SQL，且天然就是
    # LIMIT/OFFSET，不会把整个结果集查出来。
    queryset = queryset[:FREE_TIER_ROW_CAP] if locked else queryset
    paginator = Paginator(queryset, 30)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # 分页链接：保留当前筛选+排序条件，只换页码——直接复用 request.GET
    # 本身（不用手动列一遍筛选参数名），这样以后再加新筛选项不用记得同步改
    # 这里。
    page_params = request.GET.copy()
    page_params.pop("page", None)
    extra_qs = page_params.urlencode()

    # 排序表头链接：保留当前筛选条件，换排序字段+方向（同一列再点一次就
    # 反向）——同样直接复用 request.GET，只去掉 sort/dir/page 这三个。
    active_filters = request.GET.copy()
    active_filters.pop("sort", None)
    active_filters.pop("dir", None)
    active_filters.pop("page", None)
    sort_urls = {}
    for key in EXPLORER_SORT_FIELDS:
        next_dir = "asc" if (key == sort_key and sort_dir == "desc") else "desc"
        params = active_filters.copy()
        params["sort"] = key
        params["dir"] = next_dir
        sort_urls[key] = "?" + params.urlencode()

    all_categories = sorted(
        Extension.objects.exclude(category="").values_list("category", flat=True).distinct()
    )
    all_payment_types = sorted(
        Extension.objects.exclude(payment_type="").values_list("payment_type", flat=True).distinct()
    )

    numeric_filter_defs = [
        {"param": param, "label": label, "options": options, "value": numeric_values[param]}
        for param, label, field_path, direction, options in EXPLORER_NUMERIC_FILTERS
    ]
    bool_filter_defs = [
        {"param": param, "label": label, "value": bool_values[param]}
        for param, label, field_name in EXPLORER_BOOL_FILTERS
    ]

    context = {
        "page_obj": page_obj,
        "total_count": total_count,
        "locked": locked,
        "extra_qs": extra_qs,
        "filters": {
            "category": category, "payment_type": payment_type, "opportunity": opportunity,
            **numeric_values, **bool_values,
        },
        "all_categories": all_categories,
        "all_payment_types": all_payment_types,
        "numeric_filter_defs": numeric_filter_defs,
        "bool_filter_defs": bool_filter_defs,
        "opportunity_options": EXPLORER_OPPORTUNITY_OPTIONS,
        "sort_fields": EXPLORER_SORT_FIELDS,
        "sort_urls": sort_urls,
        "current_sort": sort_key,
        "current_dir": sort_dir,
        "meta_title": "Market Explorer — Extension Intelligence",
        "meta_description": "Filter Chrome extensions by user count, growth, rating, rank, Featured status, and opportunity signals.",
    }
    return render(request, "intelligence/explorer.html", context)


def _growth_summary_sentence(metric):
    """基于真实的7D/30D/90D数据生成一句自然语言摘要（英文）——只用非空字段
    拼句子，缺哪个窗口就跳过哪个，不编造。metric为None或三个窗口全空时返回
    None，调用方据此决定要不要展示这一段/回答对应FAQ。2026-09-30：全站改成
    纯英文之后，这个函数不再需要中英文两个版本（之前分成
    _growth_summary_sentence/_growth_summary_sentence_en，是因为FAQ英文、
    主页面中文；现在两处场景语言统一了，合并成一个）。"""
    if metric is None:
        return None
    parts = []
    for days, change, rate in (
        (7, metric.user_count_change_7d, metric.user_growth_rate_7d),
        (30, metric.user_count_change_30d, metric.user_growth_rate_30d),
        (90, metric.user_count_change_90d, metric.user_growth_rate_90d),
    ):
        if change is None:
            continue
        direction = "grew by" if change >= 0 else "declined by"
        piece = f"{direction} {abs(change):,} users over the past {days} days"
        if rate is not None:
            piece += f" ({rate * 100:+.1f}%)"
        parts.append(piece)
    if not parts:
        return None
    return ", ".join(parts) + "."


def _build_faq(extension, metric):
    """Question → Direct Answer：只回答数据真正支持的问题（用户数/是否增长/
    评分/最后更新时间/历史增长趋势），不回答"是否安全""是否值得安装""是否
    比X更好"这类需要额外信息源（代码审计、横向评测）才能负责任回答的问题
    ——这几类我们现在的数据完全不支持，回答了就是编。任何一题依赖的字段
    缺失就跳过那一题，不用默认值/占位答案凑数。"""
    faq = []
    if metric is not None and metric.user_count is not None:
        faq.append((
            f"How many users does {extension.name} have?",
            f"{extension.name} currently has {metric.user_count:,} users, based on our latest daily snapshot.",
        ))

    growth_sentence = _growth_summary_sentence(metric)
    if metric is not None:
        change = metric.user_count_change_30d
        if change is None:
            change = metric.user_count_change_7d
        if change is None:
            change = metric.user_count_change_90d
        if change is not None:
            trend = "growing" if change > 0 else ("shrinking" if change < 0 else "roughly flat in user count")
            faq.append((
                f"Is {extension.name} growing?",
                f"Based on our tracked data, {extension.name} is currently {trend}."
                + (f" {growth_sentence}" if growth_sentence else ""),
            ))

    if extension.rating_value is not None and extension.rating_count:
        faq.append((
            f"How is {extension.name} rated?",
            f"{extension.name} has an average rating of {extension.rating_value:.1f} out of 5, "
            f"based on {extension.rating_count:,} reviews.",
        ))

    if extension.last_update:
        faq.append((
            f"When was {extension.name} last updated?",
            f"{extension.name} was last updated on {extension.last_update}.",
        ))

    if metric is not None and metric.age_days and metric.age_days >= 30:
        faq.append((
            f"How has {extension.name} grown over time?",
            f"We have tracked {extension.name} for {metric.age_days} days. "
            + (growth_sentence or "See the historical chart above for its full user growth trend."),
        ))

    return faq


# 首发只做了Productivity一个分类页面——面包屑/内链只有分类真的匹配这个
# 已上线的分类页时才生成链接，其它分类就是纯文本，不指向一个不存在的URL。
_LINKED_CATEGORY_URL_NAMES = {"productivity": "intelligence:category_productivity"}


def _similar_extensions(extension, limit=6):
    """同分类、同样通过Tier1门槛的插件，按当前用户数排序——不是"相似度算法"，
    是最基础、最容易验证的"同类对标"关系，避免过度设计一个没有数据支撑的
    相似度评分。"""
    if not extension.category:
        return []
    return list(
        Extension.objects.filter(category=extension.category, seo_tier=Extension.SEO_TIER_TIER1)
        .exclude(extension_id=extension.extension_id)
        .select_related("metric")
        .order_by("-metric__user_count")[:limit]
    )


def extension_detail(request, extension_id, slug):
    """
    Extension Detail 页面。

    当前状态 + 增长指标 + 历史曲线 + 首次里程碑：全部来自 MySQL
    （由 sync_extension 命令预先同步/预计算进来），页面本身不碰 Parquet、
    不现场调用 plugin_history —— 那一步慢（40万插件规模下约1分钟），
    只应该在同步时做一次，不应该发生在网页请求路径上。

    SEO收录状态（robots meta/是否算tier1）完全依赖 Extension.seo_tier
    这个预计算好的字段（由 compute_seo_tier 命令每天更新），这个视图
    本身不做任何"临时判断该不该收录"的逻辑，保证跟sitemap的收录范围
    始终一致（同一个字段驱动两处，不会出现"页面说index但sitemap里没有"
    这种自相矛盾）。
    """
    try:
        extension = Extension.objects.select_related("metric", "chart").get(extension_id=extension_id)
    except Extension.DoesNotExist:
        raise Http404(
            f"Extension {extension_id} isn't in the database yet. Run:\n"
            f"python manage.py sync_extension {extension_id}"
        )

    metric = getattr(extension, "metric", None)
    chart = getattr(extension, "chart", None)
    history_rows = chart.history if chart else []
    canonical_url = request.build_absolute_uri(request.path)
    should_index = extension.seo_tier in (Extension.SEO_TIER_TIER1, Extension.SEO_TIER_TIER1_GRACE)

    # JSON-LD 结构化数据。2026-09-30移除了aggregateRating——那是把Chrome
    # Web Store第三方的评分数据包装成"本站的AggregateRating"，这不是我们
    # 自己产生的评价数据，不应该用structured data替别人的评分背书。评分/
    # 评论数依然在页面上正常展示给用户看，只是不再进这段JSON-LD。
    jsonld = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": extension.name,
        "applicationCategory": "BrowserApplication",
        "operatingSystem": "Chrome",
        "url": canonical_url,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
    }
    if extension.developer:
        jsonld["author"] = {"@type": "Organization", "name": extension.developer}

    # BreadcrumbList——分类只有对应到我们真的已经上线的分类页面时才生成
    # 链接（首发只有Productivity这一个），其它分类保持纯文本，不指向一个
    # 不存在的URL。
    category_key = (extension.category or "").strip().lower()
    category_url_name = _LINKED_CATEGORY_URL_NAMES.get(category_key)
    breadcrumb_items = [{"name": "Home", "url": request.build_absolute_uri(reverse("intelligence:home"))}]
    if extension.category:
        breadcrumb_items.append({
            "name": extension.category,
            "url": request.build_absolute_uri(reverse(category_url_name)) if category_url_name else None,
        })
    breadcrumb_items.append({"name": extension.name, "url": canonical_url})

    breadcrumb_jsonld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem", "position": i + 1, "name": item["name"],
                **({"item": item["url"]} if item["url"] else {}),
            }
            for i, item in enumerate(breadcrumb_items)
        ],
    }

    faq = _build_faq(extension, metric)

    context = {
        "extension": extension,
        "metric": metric,
        "history_rows": history_rows,
        "chart_path": _sparkline_path(history_rows, "user_count") if history_rows else "",
        "milestone_rows": chart.milestones if chart else [],
        "canonical_url": canonical_url,
        "should_index": should_index,
        "breadcrumb_items": breadcrumb_items,
        "growth_summary": _growth_summary_sentence(metric),
        "faq": faq,
        "similar_extensions": _similar_extensions(extension),
        "category_url_name": category_url_name,
        # replace("</", "<\\/") 防止字段里万一出现 "</script>" 提前截断这段JSON-LD脚本标签。
        "jsonld": mark_safe(json.dumps(jsonld).replace("</", "<\\/")),
        "breadcrumb_jsonld": mark_safe(json.dumps(breadcrumb_jsonld).replace("</", "<\\/")),
        "meta_title": f"{extension.name} — User Data & Growth History | Extension Intelligence",
        "meta_description": (
            f"User growth, ratings, and historical trend data for {extension.name} ({extension.category or 'Uncategorized'})."
        ),
        "page_type": "seo_landing",
    }
    return render(request, "intelligence/extension_detail.html", context)


def pricing(request):
    """
    Pricing 页面。三档方案的文案直接写在模板里（你之前确认过的定位：
    免费体验 / 专业版自主研究 / 定制情报版数据+服务）。

    真实Stripe接入后：这里需要传当前用户的真实方案，让页面上正确显示
    "当前方案" vs "升级"（不能像以前那样写死显示免费版是当前方案）。
    """
    current_plan = UserProfile.PLAN_FREE
    if request.user.is_authenticated:
        profile = getattr(request.user, "profile", None)
        if profile is not None:
            current_plan = profile.plan

    context = {
        "current_plan": current_plan,
        "meta_title": "Pricing — Extension Intelligence",
        "meta_description": "From free exploration to the full data asset — three plans for different levels of market research depth.",
        "page_type": "pricing",
    }
    return render(request, "intelligence/pricing.html", context)


def privacy(request):
    """隐私政策页面——静态内容，不需要传context数据。"""
    context = {
        "meta_title": "Privacy Policy — Extension Intelligence",
        "meta_description": "How Extension Intelligence collects, uses, and protects your personal information.",
        "page_type": "other",
    }
    return render(request, "intelligence/privacy.html", context)


def terms(request):
    """服务条款页面——静态内容，不需要传context数据。"""
    context = {
        "meta_title": "Terms of Service — Extension Intelligence",
        "meta_description": "Terms to read before using the Extension Intelligence service.",
        "page_type": "other",
    }
    return render(request, "intelligence/terms.html", context)


def methodology(request):
    """方法论页面——解释真实的数据计算方式，内容直接取自metrics.py/
    compute_seo_tier.py等真实代码的实际逻辑，不是泛泛而谈。"""
    context = {
        "meta_title": "Methodology — Extension Intelligence",
        "meta_description": "How we actually calculate user growth rate, opportunity signals, milestone speed, and the SEO Index Pool.",
        "page_type": "seo_landing",
    }
    return render(request, "intelligence/methodology.html", context)


def data_dictionary(request):
    """数据字典页面——完整70字段说明，字段清单直接取自
    generate_customer_dataset.py 的 CORE_FIELDS/METRIC_FIELDS 常量和
    raw_csv_reader.py 的 RAW_FIELD_MAP，跟真实产出的Customer Dataset
    Schema保证一致，不是另外维护一份可能会跟代码脱节的文档。"""
    from .raw_csv_reader import NEW_RAW_FIELDS

    core_fields = [
        ("id", "String", "Extension's unique ID (Chrome Web Store extension ID)"),
        ("name", "String", "Extension name"),
        ("item_category", "String", "extension / theme / application"),
        ("user_count", "Integer", "Current user count"),
        ("rating_value", "Float", "Current average rating (0-5)"),
        ("rating_count", "Integer", "Current total review count"),
        ("version", "String", "Current version number"),
        ("last_update", "Date", "Last update date"),
        ("creation_date", "Date", "Date first listed"),
        ("author", "String", "Developer/publisher name"),
        ("author_id", "String", "Developer ID"),
        ("category", "String", "Category"),
        ("payment_type", "String", "Business model (Free/Paid/Freemium, etc.)"),
        ("supported_languages", "String", "List of supported languages"),
        ("num_screenshots", "Integer", "Number of screenshots on the store page"),
        ("num_videos", "Integer", "Number of videos on the store page"),
        ("is_featured", "Boolean", "Whether flagged as Featured by the Chrome Web Store"),
        ("resurrection_date", "Date", "Date relisted, if previously delisted and later relisted"),
        ("previous_obsolete_date", "Date", "Date of the most recent delisting"),
        ("is_unlisted", "Boolean", "Whether currently delisted/unlisted"),
        ("extension_rank", "Integer", "Rank within its category"),
        ("overall_rank", "Integer", "Site-wide rank"),
        ("is_trusted_publisher", "Boolean", "Whether a Chrome-verified Trusted Publisher"),
        ("by_google", "Boolean", "Whether published officially by Google"),
        ("email", "String", "Developer contact email (if public)"),
    ]
    metric_fields = [
        ("user_count_change_1d/3d/7d/30d/90d/180d", "Integer", "User count change over the corresponding window; NULL (not 0) when there isn't enough history"),
        ("user_growth_rate_1d/7d/30d/90d/180d", "Float", "User growth rate over the corresponding window"),
        ("user_count_prior_7d_change / growth_acceleration_7d", "Integer/Float", "Change over the prior 7-day window, and this 7-day window's acceleration relative to it"),
        ("user_count_prior_30d_change / growth_acceleration_30d", "Integer/Float", "Same as above, for the 30-day window"),
        ("rating_count_change_1d/7d/30d/90d", "Integer", "Change in review count"),
        ("rating_value_change_30d/90d", "Float", "Change in rating"),
        ("extension_rank_change_1d/7d/30d", "Integer", "Change in category rank"),
        ("overall_rank_change_1d/7d/30d", "Integer", "Change in site-wide rank"),
        ("age_days", "Integer", "Days since the extension first appeared in our data"),
        ("days_since_update", "Integer", "Days since the last update"),
    ]
    raw_field_descriptions = {
        "description": "Extension's functional description (raw text)",
        "website": "Developer's official website",
        "raw_author_name": "Raw developer name field",
        "publisher_country": "Publisher's country/region",
        "url": "Chrome Web Store detail page URL",
        "logo": "Icon image URL",
        "privacy_policy_url": "Privacy policy link",
        "publisher_address": "Publisher address (if public)",
        "help_url": "Help/support link",
        "size": "Install package size",
        "small_banner": "Small promo image URL",
        "marquee_banner": "Large promo image URL",
        "is_mature": "Whether flagged as mature content",
        "theme_rank": "Theme-category rank (only set when item_category=theme)",
        "application_rank": "Application-category rank (only set when item_category=application)",
    }
    raw_fields = [(name, "String", raw_field_descriptions.get(name, "")) for name in NEW_RAW_FIELDS]

    context = {
        "core_fields": core_fields,
        "metric_fields": metric_fields,
        "raw_fields": raw_fields,
        "total_field_count": len(core_fields) + len(metric_fields) + len(raw_fields) + 1,  # +1 是 snapshot_date
        "meta_title": "Data Dictionary — Full Field Reference | Extension Intelligence",
        "meta_description": "A complete reference for all 70 fields in the Chrome Extension Dataset: core fields, growth metrics, and product metadata fields.",
        "page_type": "seo_landing",
    }
    return render(request, "intelligence/data_dictionary.html", context)


def about(request):
    """About页面——如实介绍产品和数据来源，不编造公司历史/团队背景（我们
    没有这些信息）。"""
    context = {
        "meta_title": "About — Extension Intelligence",
        "meta_description": "What Extension Intelligence is, where the data comes from, and how we calculate these metrics.",
        "page_type": "other",
    }
    return render(request, "intelligence/about.html", context)


def contact(request):
    """Contact页面——目前唯一的联系渠道就是客服邮箱，人工处理，没有在线
    工单系统（跟登录页"联系客服"链接、Account页取消订阅走客服邮箱是同一套
    设计原则：人工介入，不做自助流程）。"""
    context = {
        "meta_title": "Contact — Extension Intelligence",
        "meta_description": "Have a question? Get in touch with the Extension Intelligence support team.",
        "page_type": "other",
    }
    return render(request, "intelligence/contact.html", context)


def _tier1_productivity_queryset():
    return (
        Extension.objects.filter(category__iexact="Productivity", seo_tier=Extension.SEO_TIER_TIER1)
        .select_related("metric")
        .order_by("-metric__user_count")
    )


def category_productivity(request):
    """Productivity分类浏览页——首发只做这一个类目验证效果。跟下面的
    best_productivity()故意区分开：这里是完整分页列表（中性、目录式），
    best_productivity是精选前20（编辑式框架），避免两个页面渲染出几乎
    一样的表格造成重复内容。"""
    queryset = _tier1_productivity_queryset()
    paginator = Paginator(queryset, 30)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    context = {
        "page_obj": page_obj,
        "total_count": paginator.count,
        "meta_title": "Productivity Chrome Extensions — Full Category Directory | Extension Intelligence",
        "meta_description": f"Browse all {paginator.count} quality-filtered Productivity-category Chrome extensions, sorted by current user count.",
        "page_type": "seo_landing",
    }
    return render(request, "intelligence/category_productivity.html", context)


BEST_PRODUCTIVITY_LIMIT = 20


def best_productivity(request):
    """Best Productivity Chrome Extensions——精选榜，按当前用户数排序取
    前20（不是发明一个不透明的"综合评分"，用户数是这个产品里最基础、最
    容易验证的排序依据）。跟category_productivity()的区别见那边的注释。"""
    top_extensions = list(_tier1_productivity_queryset()[:BEST_PRODUCTIVITY_LIMIT])

    context = {
        "extensions": top_extensions,
        "meta_title": "Best Productivity Chrome Extensions (2026) — Extension Intelligence",
        "meta_description": f"The top {len(top_extensions)} Productivity-category Chrome extensions ranked by real user count, updated daily.",
        "page_type": "seo_landing",
    }
    return render(request, "intelligence/best_productivity.html", context)


def research_index(request):
    from .research_content import get_all_articles

    context = {
        "articles": get_all_articles(),
        "meta_title": "Research & Insights — Extension Intelligence",
        "meta_description": "Research and analysis grounded in real Chrome Extension market data.",
        "page_type": "research",
    }
    return render(request, "intelligence/research_index.html", context)


def research_detail(request, slug):
    import re

    from .research_content import get_article_by_slug

    article = get_article_by_slug(slug)
    if article is None:
        raise Http404(f"No research article found: {slug}")

    # research_content.py里用**text**标记加粗（纯我们自己写的静态内容，不是
    # 用户输入），转成<strong>后mark_safe——不引入完整markdown库，只处理这
    # 一种最简单的标记，够用。
    def render_bold(text):
        return mark_safe(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text))

    body_html = [render_bold(p) for p in article["body"]]

    context = {
        "article": article,
        "body_html": body_html,
        "meta_title": f"{article['title']} — Extension Intelligence Research",
        "meta_description": article["meta_description"],
        "page_type": "research",
    }
    return render(request, "intelligence/research_detail.html", context)


def register(request):
    """极简注册：邮箱+密码。注册成功自动创建 UserProfile（默认Free方案）
    并直接登录，不做邮箱验证这类复杂流程（原设计明确要求"保持极简"）。"""
    if request.user.is_authenticated:
        return redirect("intelligence:account")

    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            password = form.cleaned_data["password"]
            user = User.objects.create_user(username=email, email=email, password=password)
            UserProfile.objects.create(user=user, plan=UserProfile.PLAN_FREE)
            # 2026-09-30发现的严重遗留bug修复：AUTHENTICATION_BACKENDS里有两个
            # backend（ModelBackend + allauth），auth_login()在这种情况下必须
            # 显式指定backend参数，否则直接抛ValueError——也就是说自从接入
            # django-allauth(支持Google登录)之后，邮箱+密码注册这条路径的
            # auth_login()这一行只要真的执行到就必然崩溃返回500，注册功能
            # 实际上完全不可用。这里显式指定用ModelBackend（邮箱+密码这条
            # 认证路径本来就是靠这个backend验证的，不是allauth那个）。
            auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            # ?signup=success 给account.html触发一次signup_completed埋点，
            # 跟Stripe那边?checkout=success触发purchase的做法一致。
            return redirect(f"{reverse('intelligence:account')}?signup=success")
    else:
        form = RegisterForm()

    context = {
        "form": form,
        "meta_title": "Sign up — Extension Intelligence",
        "meta_description": "Create an account and start exploring Chrome extension market data.",
        "page_type": "other",
    }
    return render(request, "intelligence/register.html", context)


@login_required
def account(request):
    """
    Account 页面。当前方案 / 数据下载权限说明 / 定制版专属的研究需求表单。

    2026-09-29 二次重新设计（你反馈"管理账单"按钮会让用户自己经Stripe Portal
    把订阅取消掉，等于绕开了"方案A"想要的人工介入）：彻底不在这个页面放
    Stripe Billing Portal 的入口，改成订阅中/免费版都只显示简单状态文案
    （不展示订阅开始日期/下次扣款日期——这两个字段仍然写在UserProfile里，
    以后如果要恢复展示随时能拿到，只是这次不在页面露出），下方新增"套餐
    包含"区块（复用Pricing页同款功能列表文案），让页面看起来不这么空。
    更新付款方式/取消订阅/任何账单问题，统一走"联系客服"人工处理。
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # 2026-09-29 统一措辞：Professional明确说"1年内"，Custom明确说"3年以上"，
    # 不再用"最近约1年"/"覆盖至2023年起"这种模糊或者会过时的说法（"2023年起"
    # 这种绝对年份会随时间推移含义变化，比如现在其实已经是3年多了）。
    # 下载入口是 Dataset 页面（intelligence:dataset），这里只做文字说明。
    download_text = {
        UserProfile.PLAN_FREE: "You can download a 50-row Sample CSV (full 70-field schema). Upgrade to unlock the full history.",
        UserProfile.PLAN_PROFESSIONAL: "You can download the full 1-year history (one 70-field file per day).",
        UserProfile.PLAN_CUSTOM: "You can download the full 3+ year history (one 70-field file per day, based on how much history has actually been backfilled).",
    }[profile.plan]

    research_form = None
    if profile.plan == UserProfile.PLAN_CUSTOM:
        if request.method == "POST":
            research_form = ResearchRequestForm(request.POST, request.FILES)
            if research_form.is_valid():
                req = research_form.save(commit=False)
                req.user = request.user
                req.save()
                messages.success(request, "Submitted — we'll be in touch within 1-2 business days.")
                research_form = ResearchRequestForm()
        else:
            research_form = ResearchRequestForm()

    context = {
        "profile": profile,
        "plan_label": profile.get_plan_display(),
        "plan_features": PLAN_FEATURES[profile.plan],
        "download_text": download_text,
        "research_form": research_form,
        "meta_title": "Account — Extension Intelligence",
        "meta_description": "View your current plan and account settings.",
        # Stripe Checkout成功后会跳转回 /account/?checkout=success——这个
        # 瞬间就是PostHog漏斗里"Purchase"阶段的落地页，page_type跟着这个
        # query参数变化，其它时候Account页就是普通"other"类型页面。
        "page_type": "purchase" if request.GET.get("checkout") == "success" else "other",
    }
    return render(request, "intelligence/account.html", context)


@staff_member_required
def staff_dashboard(request):
    """
    自己写的轻量内部管理页——不是Django admin的替代品（Django admin还在，
    有更复杂的排查需求时可以用），这里只覆盖你实际日常要做的两件事：
    改用户方案、看定制研究需求提交。只有 is_staff 账号能访问。
    """
    profiles = UserProfile.objects.select_related("user").order_by("-created_at")
    research_requests = ResearchRequest.objects.select_related("user").order_by("-created_at")

    context = {
        "profiles": profiles,
        "requests": research_requests,
        "plan_choices": UserProfile.PLAN_CHOICES,
        "meta_title": "内部管理 — Extension Intelligence",
        "meta_description": "内部管理页面，不对外公开。",
    }
    return render(request, "intelligence/staff_dashboard.html", context)


@staff_member_required
def staff_update_plan(request, user_id):
    """从内部管理页提交，直接改某个用户的方案。GET 直接跳回去，只处理POST。"""
    if request.method == "POST":
        profile = get_object_or_404(UserProfile, user_id=user_id)
        new_plan = request.POST.get("plan")
        if new_plan in dict(UserProfile.PLAN_CHOICES):
            profile.plan = new_plan
            profile.save()
            messages.success(
                request,
                f"已把 {profile.user.email or profile.user.username} 的方案改成 {profile.get_plan_display()}",
            )
    return redirect("intelligence:staff_dashboard")
