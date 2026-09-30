import json
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import F
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe

from . import permissions
from .customer_dataset_access import generate_download_url, get_entitled_dates, list_oss_dataset_dates
from .forms import RegisterForm, ResearchRequestForm
from .legacy_bridge import get_snapshot_store
from .models import Extension, ResearchRequest, UserProfile
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
    "1d_growth": {"mode": "growth", "field": "user_count_change_1d", "label": "1日增长", "value_label": "1日增长", "is_percent": False},
    "7d_growth": {"mode": "growth", "field": "user_count_change_7d", "label": "7日增长", "value_label": "7日增长", "is_percent": False},
    "30d_growth": {"mode": "growth", "field": "user_count_change_30d", "label": "30日增长", "value_label": "30日增长", "is_percent": False},
    "7d_rate": {"mode": "growth", "field": "user_growth_rate_7d", "label": "7日增长率", "value_label": "7日增长率", "is_percent": True},
    "30d_rate": {"mode": "growth", "field": "user_growth_rate_30d", "label": "30日增长率", "value_label": "30日增长率", "is_percent": True},
    "opp_emerging": {
        "mode": "opportunity", "field": "user_count_change_30d", "label": "新兴增长",
        "value_label": "30日增长", "is_percent": False, "subtitle": "上线时间较短，但已经快速获得用户。",
    },
    "opp_accelerating": {
        "mode": "opportunity", "field": "user_count_growth_acceleration_7d", "label": "增长加速",
        "value_label": "7日增长加速度", "is_percent": False, "subtitle": "最近的增长速度明显高于上一周期。",
    },
    "opp_under_radar": {
        "mode": "opportunity", "field": "user_growth_rate_30d", "label": "低关注高增长",
        "value_label": "30日增长率", "is_percent": True, "subtitle": "当前规模仍有限，但增长表现已经明显突出。",
    },
    "fast_1000": {
        "mode": "milestone", "threshold": 1000, "field": "days_to_1000",
        "label": "最快达到1K用户", "milestone_label": "1K", "subtitle": "按创建至首次达到1,000用户所需天数排序。",
    },
    "fast_10000": {
        "mode": "milestone", "threshold": 10000, "field": "days_to_10000",
        "label": "最快达到10K用户", "milestone_label": "10K", "subtitle": "按创建至首次达到10,000用户所需天数排序。",
    },
    "fast_100000": {
        "mode": "milestone", "threshold": 100000, "field": "days_to_100000",
        "label": "最快达到100K用户", "milestone_label": "100K", "subtitle": "按创建至首次达到100,000用户所需天数排序。",
    },
}

# 分组结构——对应设计稿的 RANKING_GROUPS_DEF，页面上按组展示Segmented选择器。
RANKING_GROUPS = [
    {"label": "增长排行", "items": [("1d_growth", "1日"), ("7d_growth", "7日"), ("30d_growth", "30日")]},
    {"label": "增长率排行", "items": [("7d_rate", "7日"), ("30d_rate", "30日")]},
    {"label": "机会信号", "items": [("opp_emerging", "新兴增长"), ("opp_accelerating", "增长加速"), ("opp_under_radar", "低关注高增长")]},
    {"label": "里程碑速度", "items": [("fast_1000", "1K"), ("fast_10000", "10K"), ("fast_100000", "100K")]},
]

# 机会信号组免费版行数上限比其它类型更严格（对照设计稿：机会类只给3条，
# 其它给 FREE_TIER_ROW_CAP=10 条）——机会信号是这个产品更"高价值"的洞察，
# 免费版少给一点，更能体现专业版的价值。
FREE_OPPORTUNITY_ROW_CAP = 3

# Market Explorer 的排序字段——跟 Rankings 是两回事：Rankings 是预定义榜单，
# Explorer 是用户自己筛选+排序的工作区。
EXPLORER_SORT_FIELDS = {
    "users": ("metric__user_count", "用户数"),
    "7d_growth": ("metric__user_count_change_7d", "7日增长"),
    "30d_growth": ("metric__user_count_change_30d", "30日增长"),
    "rating": ("rating_value", "评分"),
    "age": ("metric__age_days", "年龄"),
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
    ("min_users", "最低用户数", "metric__user_count", "gte",
     [("100", "100+"), ("1000", "1K+"), ("10000", "10K+"), ("100000", "100K+")]),
    ("min_rating", "最低评分", "rating_value", "gte",
     [("4", "4.0+"), ("4.5", "4.5+")]),
    ("min_reviews", "最低评论数", "rating_count", "gte",
     [("10", "10+"), ("100", "100+"), ("1000", "1K+"), ("10000", "10K+")]),
    ("min_age", "插件年龄（最低）", "metric__age_days", "gte",
     [("30", "30天+"), ("180", "180天+"), ("365", "1年+"), ("730", "2年+")]),
    ("max_days_since_update", "最后更新距今", "metric__days_since_update", "lte",
     [("7", "7天内"), ("30", "30天内"), ("90", "90天内")]),
    ("min_g1d", "1日增长（最低）", "metric__user_count_change_1d", "gte",
     [("0", "正增长"), ("100", "100+"), ("1000", "1K+")]),
    ("min_g7d", "7日增长（最低）", "metric__user_count_change_7d", "gte",
     [("0", "正增长"), ("500", "500+"), ("5000", "5K+")]),
    ("min_g30d", "30日增长（最低）", "metric__user_count_change_30d", "gte",
     [("0", "正增长"), ("1000", "1K+"), ("10000", "10K+")]),
    ("min_rate7d", "7日增长率（最低）", "metric__user_growth_rate_7d", "gte",
     [("0", "正增长"), ("0.1", "10%+"), ("0.3", "30%+")]),
    ("min_rate30d", "30日增长率（最低）", "metric__user_growth_rate_30d", "gte",
     [("0", "正增长"), ("0.1", "10%+"), ("0.5", "50%+")]),
    ("max_ext_rank", "Extension Rank", "extension_rank", "lte",
     [("100", "前100"), ("1000", "前1000"), ("10000", "前1万"), ("100000", "前10万")]),
    ("max_overall_rank", "Overall Rank", "overall_rank", "lte",
     [("100", "前100"), ("1000", "前1000"), ("10000", "前1万"), ("100000", "前10万")]),
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
    ("opp_emerging", "新兴增长"),
    ("opp_accelerating", "增长加速"),
    ("opp_under_radar", "低关注高增长"),
]

# 当前 parquet 里真实存在的25个字段，按用途分组，仅供 Dataset 页面展示用。
# 跟 snapshot_store.EXPECTED_COLUMNS 保持一致——这里没有 import 那个列表，
# 是因为这是"展示分组"，属于页面文案范畴，不是数据校验逻辑，两者职责不同。
FIELD_SCHEMA = [
    {"group": "插件信息", "fields": ["id", "name", "category", "item_category", "author", "author_id", "email"]},
    {"group": "商店数据", "fields": ["version", "creation_date", "last_update", "payment_type",
                                   "supported_languages", "num_screenshots", "num_videos"]},
    {"group": "状态标记", "fields": ["is_featured", "is_trusted_publisher", "by_google", "is_unlisted",
                                   "resurrection_date", "previous_obsolete_date"]},
    {"group": "增长与排名", "fields": ["user_count", "rating_value", "rating_count", "extension_rank", "overall_rank"]},
]

# Account页面"套餐包含"区块用——文案照抄Pricing页每档方案下面的功能列表，
# 保持两处说法一致。这里没有跟pricing.html共享同一份数据结构（那边是直接
# 写死在模板里的），以后要改哪档的功能描述，两处都要记得改，这是当前规模
# 下可以接受的重复（不是很多地方引用，改的频率也不高）。
# 2026-09-29：套餐日期范围统一用"1年内"（Professional）/"3年以上"（Custom）
# 这两个精确说法，不再用"近1年"这种模糊表述——同样的措辞也要跟Pricing页
# 保持一致（那边是写死在模板里的，没有共享这份数据）。
PLAN_FEATURES = {
    "free": ["搜索插件", "基础插件信息", "排行榜预览（有限结果）", "机会信号预览（Top 3）", "30 天历史分析", "数据集示例数据"],
    "professional": ["完整排行榜与机会榜", "高级市场探索筛选", "完整插件历史分析", "同类对标与里程碑分析", "Chrome Extension 数据集访问（1年内历史）", "数据集历史导出"],
    "custom": ["专业版全部功能", "完整历史数据集访问权限（3年以上）", "定制市场与关键词研究", "Chrome Web Store 关键词排名研究", "竞争对手与本地化研究", "自定义 CSV / Excel 报告"],
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
        "name": "示例插件（演示数据，非真实插件）",
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
    query = request.GET.get("q", "").strip()
    search_results = Extension.objects.filter(name__icontains=query)[:10] if query else []

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
        "meta_title": "Extension Intelligence — Chrome插件市场情报平台",
        "meta_description": "发现正在增长的Chrome插件，查看历史数据与增长趋势。",
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

    context = {
        "total_dates": len(dates),
        "earliest_date": dates[0] if dates else None,
        "latest_date": dates[-1] if dates else None,
        "recent_dates": list(reversed(dates))[:10],
        "field_schema": FIELD_SCHEMA,
        "sample_date": latest,
        "sample_rows": sample_rows,
        "sample_hidden_field_count": 70 - len(preview_columns),
        "viewer_plan": viewer_plan,
        "entitled_dates": entitled_dates,
        "downloads_unavailable": downloads_unavailable,
        "meta_title": "Chrome Extension Dataset — 完整历史数据集 | Extension Intelligence",
        "meta_description": "300K+ Chrome插件的每日历史数据，标准化字段，支持CSV导出。",
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
        return HttpResponse("示例数据集尚未配置。", status=503)

    sample_path = Path(settings.CUSTOMER_DATASET_DIR) / "sample.csv"
    if not sample_path.exists():
        return HttpResponse(
            "示例数据集尚未生成，请联系管理员运行 python manage.py generate_customer_dataset_sample。",
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
        messages.error(request, "下载服务暂时不可用，请稍后再试。")
        return redirect("intelligence:dataset")

    entitled = get_entitled_dates(viewer_plan, oss_dates)
    if date_str not in entitled:
        messages.error(request, "你当前的套餐没有这个日期的下载权限。")
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

    visible = list(queryset[:cap]) if locked else list(queryset)
    if mode == "milestone":
        date_field = f"date_to_{config['threshold']}"
        rows = [
            {
                "rank": i + 1, "extension": ext,
                "days": getattr(ext.metric, field),
                "reached_date": getattr(ext.metric, date_field),
            }
            for i, ext in enumerate(visible)
        ]
    else:
        rows = [
            {"rank": i + 1, "extension": ext, "value": getattr(ext.metric, field)}
            for i, ext in enumerate(visible)
        ]

    paginator = Paginator(rows, 30)
    page_obj = paginator.get_page(request.GET.get("page", 1))

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
        "locked": locked,
        "total_count": total_count,
        # 2026-09-30确认的Rankings SEO规则：只有真正"裸"的/rankings/（没有
        # type/page这些query参数）才index；带着任何参数的状态页——不管是
        # 换了榜单类型还是翻了页——一律noindex,follow。以后哪个榜单类型真的
        # 值得单独收录，会给它做一条独立干净的URL（比如/rankings/fastest-
        # growing/），不是继续加宽这条query parameter的index范围。
        "should_index": "type" not in request.GET and "page" not in request.GET,
        "meta_title": f"{config['label']}排行榜 — Extension Intelligence",
        "meta_description": f"查看Chrome插件{config['label']}排行榜，共{total_count}个插件。",
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

    numeric_values = {}
    for param, label, field_path, direction, options in EXPLORER_NUMERIC_FILTERS:
        value = request.GET.get(param, "")
        numeric_values[param] = value
        if value:
            try:
                queryset = queryset.filter(**{f"{field_path}__{direction}": float(value)})
            except ValueError:
                # 手动改URL传了非法值（正常点下拉不会出现）——忽略这个筛选
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
    visible = list(queryset[:FREE_TIER_ROW_CAP]) if locked else list(queryset)

    paginator = Paginator(visible, 30)
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
        "meta_title": "市场探索 Market Explorer — Extension Intelligence",
        "meta_description": "按用户数、增长、评分、排名、Featured、机会信号等条件筛选Chrome插件。",
    }
    return render(request, "intelligence/explorer.html", context)


def _growth_summary_sentence(metric):
    """基于真实的7D/30D/90D数据生成一句自然语言摘要——只用非空字段拼句子，
    缺哪个窗口就跳过哪个，不编造。metric为None或三个窗口全空时返回None，
    调用方据此决定要不要展示这一段/回答对应FAQ。"""
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
        direction = "增长了" if change >= 0 else "减少了"
        piece = f"过去{days}天{direction} {abs(change):,} 名用户"
        if rate is not None:
            piece += f"（{rate * 100:+.1f}%）"
        parts.append(piece)
    if not parts:
        return None
    return "，".join(parts) + "。"


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
            f"数据库里还没有插件 {extension_id}，先执行：\n"
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
    breadcrumb_items = [{"name": "首页", "url": request.build_absolute_uri(reverse("intelligence:home"))}]
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
        "meta_title": f"{extension.name} — 用户数据与增长历史 | Extension Intelligence",
        "meta_description": (
            f"{extension.name}（{extension.category or '未分类'}）的用户增长、评分与历史趋势数据分析。"
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
        "meta_title": "定价方案 Pricing — Extension Intelligence",
        "meta_description": "从免费探索到完整数据资产，三档方案对应不同的市场研究深度。",
        "page_type": "pricing",
    }
    return render(request, "intelligence/pricing.html", context)


def privacy(request):
    """隐私政策页面——静态内容，不需要传context数据。"""
    context = {
        "meta_title": "隐私政策 — Extension Intelligence",
        "meta_description": "Extension Intelligence 如何收集、使用、保护你的个人信息。",
        "page_type": "other",
    }
    return render(request, "intelligence/privacy.html", context)


def terms(request):
    """服务条款页面——静态内容，不需要传context数据。"""
    context = {
        "meta_title": "服务条款 — Extension Intelligence",
        "meta_description": "使用 Extension Intelligence 服务前请阅读的条款。",
        "page_type": "other",
    }
    return render(request, "intelligence/terms.html", context)


def methodology(request):
    """方法论页面——解释真实的数据计算方式，内容直接取自metrics.py/
    compute_seo_tier.py等真实代码的实际逻辑，不是泛泛而谈。"""
    context = {
        "meta_title": "方法论 Methodology — Extension Intelligence",
        "meta_description": "用户增长率、机会信号、里程碑速度、SEO Index Pool——这些指标到底是怎么算出来的。",
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
        ("id", "字符串", "插件唯一ID（Chrome Web Store的扩展ID）"),
        ("name", "字符串", "插件名称"),
        ("item_category", "字符串", "extension / theme / application"),
        ("user_count", "整数", "当前用户数"),
        ("rating_value", "浮点数", "当前平均评分（0-5）"),
        ("rating_count", "整数", "当前评论总数"),
        ("version", "字符串", "当前版本号"),
        ("last_update", "日期", "最后更新日期"),
        ("creation_date", "日期", "首次上架日期"),
        ("author", "字符串", "开发者/发布者名称"),
        ("author_id", "字符串", "开发者ID"),
        ("category", "字符串", "分类"),
        ("payment_type", "字符串", "商业模式（Free/Paid/Freemium等）"),
        ("supported_languages", "字符串", "支持的语言列表"),
        ("num_screenshots", "整数", "商店页面截图数量"),
        ("num_videos", "整数", "商店页面视频数量"),
        ("is_featured", "布尔", "是否被Chrome Web Store标记为Featured"),
        ("resurrection_date", "日期", "如果曾经下架后重新上架，重新上架日期"),
        ("previous_obsolete_date", "日期", "上一次被标记为下架的日期"),
        ("is_unlisted", "布尔", "是否已下架/未公开上架"),
        ("extension_rank", "整数", "同类目内排名"),
        ("overall_rank", "整数", "全站排名"),
        ("is_trusted_publisher", "布尔", "是否为Chrome认证的Trusted Publisher"),
        ("by_google", "布尔", "是否为Google官方发布"),
        ("email", "字符串", "开发者联系邮箱（如果公开）"),
    ]
    metric_fields = [
        ("user_count_change_1d/3d/7d/30d/90d/180d", "整数", "对应窗口内用户数变化量，历史窗口不够时为NULL（不是0）"),
        ("user_growth_rate_1d/7d/30d/90d/180d", "浮点数", "对应窗口用户增长率"),
        ("user_count_prior_7d_change / growth_acceleration_7d", "整数/浮点数", "上一个7天窗口的变化量，及本次7天相对上次7天的加速度"),
        ("user_count_prior_30d_change / growth_acceleration_30d", "整数/浮点数", "同上，30天窗口版本"),
        ("rating_count_change_1d/7d/30d/90d", "整数", "评论数变化量"),
        ("rating_value_change_30d/90d", "浮点数", "评分变化量"),
        ("extension_rank_change_1d/7d/30d", "整数", "分类排名变化"),
        ("overall_rank_change_1d/7d/30d", "整数", "全站排名变化"),
        ("age_days", "整数", "距首次出现在我们数据里的天数"),
        ("days_since_update", "整数", "距最后更新的天数"),
    ]
    raw_field_descriptions = {
        "description": "插件功能描述（原始文本）",
        "website": "开发者官网",
        "raw_author_name": "原始开发者名称字段",
        "publisher_country": "发布者所在国家/地区",
        "url": "Chrome Web Store详情页URL",
        "logo": "图标图片URL",
        "privacy_policy_url": "隐私政策链接",
        "publisher_address": "发布者地址（如果公开）",
        "help_url": "帮助/支持链接",
        "size": "安装包大小",
        "small_banner": "小尺寸宣传图URL",
        "marquee_banner": "大尺寸宣传图URL",
        "is_mature": "是否标记为成人内容",
        "theme_rank": "主题类目排名（仅item_category=theme时有值）",
        "application_rank": "应用类目排名（仅item_category=application时有值）",
    }
    raw_fields = [(name, "字符串", raw_field_descriptions.get(name, "")) for name in NEW_RAW_FIELDS]

    context = {
        "core_fields": core_fields,
        "metric_fields": metric_fields,
        "raw_fields": raw_fields,
        "total_field_count": len(core_fields) + len(metric_fields) + len(raw_fields) + 1,  # +1 是 snapshot_date
        "meta_title": "Data Dictionary 完整字段说明 — Extension Intelligence",
        "meta_description": "Chrome Extension Dataset全部70个字段的完整说明：核心字段、增长指标、产品资料字段。",
        "page_type": "seo_landing",
    }
    return render(request, "intelligence/data_dictionary.html", context)


def about(request):
    """About页面——如实介绍产品和数据来源，不编造公司历史/团队背景（我们
    没有这些信息）。"""
    context = {
        "meta_title": "关于我们 About — Extension Intelligence",
        "meta_description": "Extension Intelligence 是什么，数据从哪里来，我们如何计算这些指标。",
        "page_type": "other",
    }
    return render(request, "intelligence/about.html", context)


def contact(request):
    """Contact页面——目前唯一的联系渠道就是客服邮箱，人工处理，没有在线
    工单系统（跟登录页"联系客服"链接、Account页取消订阅走客服邮箱是同一套
    设计原则：人工介入，不做自助流程）。"""
    context = {
        "meta_title": "联系我们 Contact — Extension Intelligence",
        "meta_description": "有问题？联系 Extension Intelligence 客服团队。",
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
        "meta_title": "Productivity Chrome Extensions — 完整分类目录 | Extension Intelligence",
        "meta_description": f"浏览全部 {paginator.count} 个已通过质量筛选的 Productivity 分类 Chrome 插件，按当前用户数排序。",
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
        "meta_description": f"按真实用户数排名的前 {len(top_extensions)} 个 Productivity 分类 Chrome 插件，数据每日更新。",
        "page_type": "seo_landing",
    }
    return render(request, "intelligence/best_productivity.html", context)


def research_index(request):
    from .research_content import get_all_articles

    context = {
        "articles": get_all_articles(),
        "meta_title": "Research & Insights — Extension Intelligence",
        "meta_description": "基于Chrome Extension市场真实数据的研究与分析。",
        "page_type": "research",
    }
    return render(request, "intelligence/research_index.html", context)


def research_detail(request, slug):
    import re

    from .research_content import get_article_by_slug

    article = get_article_by_slug(slug)
    if article is None:
        raise Http404(f"没有这篇研究文章: {slug}")

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
        "meta_title": "注册 — Extension Intelligence",
        "meta_description": "注册账户，开始探索Chrome插件市场数据。",
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
        UserProfile.PLAN_FREE: "可以下载50行的 Sample CSV（完整70字段Schema），升级后解锁完整历史。",
        UserProfile.PLAN_PROFESSIONAL: "可下载1年内的完整历史数据（每天一个文件，70字段）。",
        UserProfile.PLAN_CUSTOM: "可下载3年以上的完整历史数据（每天一个文件，70字段，具体以实际已回补的数据为准）。",
    }[profile.plan]

    research_form = None
    if profile.plan == UserProfile.PLAN_CUSTOM:
        if request.method == "POST":
            research_form = ResearchRequestForm(request.POST, request.FILES)
            if research_form.is_valid():
                req = research_form.save(commit=False)
                req.user = request.user
                req.save()
                messages.success(request, "已提交，我们会在1-2个工作日内与你联系。")
                research_form = ResearchRequestForm()
        else:
            research_form = ResearchRequestForm()

    context = {
        "profile": profile,
        "plan_label": profile.get_plan_display(),
        "plan_features": PLAN_FEATURES[profile.plan],
        "download_text": download_text,
        "research_form": research_form,
        "meta_title": "账户 Account — Extension Intelligence",
        "meta_description": "查看当前方案与账户设置。",
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
