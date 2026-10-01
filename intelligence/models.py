"""
Phase 1 最小数据模型。

只覆盖 Extension Detail 页面需要的字段，不是最终生产 schema——
后面阶段规模扩大时（Rankings/Explorer/Dataset）会再补充字段和新表，
现在只求能撑起一个真实页面。

历史曲线：由 sync_extension 命令在同步时调用一次 plugin_history 现场扫描，
结果存进 ExtensionChart 的 JSON 字段。Detail 页只读这张表，不再现场扫描——
实测40万插件规模下现场扫描一次约1分钟，不适合放在网页请求路径上。

当前只有约1年历史数据，暂时原样存全部每日点，不做降采样；等以后合并完整
3年历史后，需要在这里补一版"近13个月按日、更早按周"的降采样逻辑。
"""
from django.contrib.auth.models import User
from django.db import models


class Extension(models.Model):
    """对应25字段快照里的"当前状态"子集，1插件1行。"""

    extension_id = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    developer = models.CharField(max_length=255, blank=True, default="")
    # 实测30万+真实数据里，category/version这些"看起来应该很短"的字段偶尔会
    # 出现异常长的值（比如 sync_all_extensions 第一次全量同步就在 version
    # 字段上撞到过 "Data too long" 报错）——这是真实数据本身的脏数据/上游解析
    # 错位，不是我们能提前预测的，所以这几个字段统一放宽到255，加上同步时的
    # 兜底截断（见 sync_helpers.py 的 _truncate），双重保险不再让个别脏行
    # 中断整批同步。
    category = models.CharField(max_length=255, blank=True, default="", db_index=True)
    # 商业模式（Free/Freemium/Paid/Subscription等）。你明确要求这个字段要向
    # 免费用户开放展示/筛选，不要因为内部研究代码判断它数据质量不可靠就不上线——
    # 那是"要不要基于它做增长趋势分析"的问题，跟"要不要展示这个原始字段"是两回事。
    payment_type = models.CharField(max_length=128, blank=True, default="")
    # 2026-09-30 新增（首发SEO内容质量要求）——原始CSV里的产品功能描述，
    # Analysis Parquet(25字段)里没有这个字段，只有raw_excel/ranking-stats-*.csv
    # 才有，走 raw_csv_reader.load_extra_fields() 读（sync_all_extensions.py
    # 里新增了这一步）。TextField不设max_length：这是自然语言描述，长度本来
    # 就不固定，不应该像version/category那样有截断需求。
    description = models.TextField(blank=True, default="")
    rating_value = models.FloatField(null=True, blank=True)
    rating_count = models.BigIntegerField(null=True, blank=True)
    version = models.CharField(max_length=255, blank=True, default="")
    creation_date = models.DateField(null=True, blank=True)
    last_update = models.DateField(null=True, blank=True)
    extension_rank = models.BigIntegerField(null=True, blank=True)
    overall_rank = models.BigIntegerField(null=True, blank=True)

    # 2026-09-29 新增（Explorer扩展第5步）——原始parquet里这三个字段存的是
    # 字符串"True"/"False"（不是原生布尔/1/0，见 sync_helpers._to_bool 的
    # 转换逻辑），null=True 是因为个别行可能缺这个值，不能直接当False处理
    # （"没有这个数据"和"明确是False"是两码事）。
    is_featured = models.BooleanField(null=True, blank=True)
    is_trusted_publisher = models.BooleanField(null=True, blank=True)
    by_google = models.BooleanField(null=True, blank=True)

    # 2026-09-30 新增（首发SEO Index Pool）——原始数据里的下架/未上架标记，
    # 同样是"True"/"False"字符串，同一套_to_bool转换。null=True：缺失值
    # 不能当作False处理。is_unlisted=true的插件页面仍然正常可访问（站内
    # 用户体验不受影响），只是不再进SEO索引池/sitemap，模板会显示一句
    # 事实型提示（不是"已知失效"这种下结论，只说当前从Chrome Web Store
    # 抓不到，避免误导）。
    is_unlisted = models.BooleanField(null=True, blank=True)

    SEO_TIER_CANDIDATE = "candidate"
    SEO_TIER_TIER1 = "tier1"
    SEO_TIER_TIER1_GRACE = "tier1_grace"
    SEO_TIER_EXCLUDED = "excluded"
    SEO_TIER_CHOICES = [
        (SEO_TIER_CANDIDATE, "Candidate (observing)"),
        (SEO_TIER_TIER1, "Tier 1 (indexed)"),
        (SEO_TIER_TIER1_GRACE, "Tier 1 grace period (still indexed)"),
        (SEO_TIER_EXCLUDED, "Excluded (noindex)"),
    ]
    # SEO索引分层状态机，由 compute_seo_tier 命令每天更新，Detail页/sitemap
    # 只读这个字段，不在请求路径上现算——具体状态转换规则见该命令文件头注释。
    # db_default（不只是default）是必须的——Django的default=只在走ORM的
    # Model()/.save()时才生效，是应用层默认值，不是数据库schema里的真正
    # DEFAULT子句（跟之前computed_at/auto_now踩过的坑是同一类问题，这次
    # 换了个字段又踩了一次：sync_all_extensions.py的_raw_bulk_upsert用原生
    # SQL INSERT，故意不包含seo_tier这一列——不然每天全量同步会把已有插件
    # 的seo_tier state machine状态全部重置回candidate。但INSERT语句完全不提
    # 这一列时，MySQL需要一个真正的列级DEFAULT才能给新插入的行填值，光有
    # Django的default=不够，2026-09-30真实MySQL环境实测直接报"Field
    # 'seo_tier' doesn't have a default value"——新插件（MySQL里从没见过的
    # extension_id）一旦出现在当天的同步批次里就会报错，老插件不受影响
    # （迁移时ALTER TABLE已经给它们回填过default值）。
    seo_tier = models.CharField(
        max_length=16, choices=SEO_TIER_CHOICES,
        default=SEO_TIER_CANDIDATE, db_default=SEO_TIER_CANDIDATE, db_index=True,
    )
    # 当前seo_tier状态从哪天开始的——tier1_grace用它计算宽限期是否到期，
    # sitemap的lastmod也用它（比每天都变的synced_at更能反映"真实重要变化"）。
    seo_tier_since = models.DateField(null=True, blank=True)
    # candidate阶段"连续达标"内部记账用，不对外展示。含义：最近一次连续
    # 合格区间从哪天开始——只要有一天不合格就清空重新计数（见compute_seo_tier）。
    seo_qualify_streak_start = models.DateField(null=True, blank=True)

    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "extension"

    def __str__(self) -> str:
        return f"{self.name} ({self.extension_id})"

    def store_url(self) -> str:
        return f"https://chromewebstore.google.com/detail/{self.extension_id}"


class ExtensionMetric(models.Model):
    """对应 metrics.py 算出的增长派生指标。只存"最新一次同步算出的结果"这一行，
    不是历史序列——这一阶段还没有每日批处理，靠 sync_extension 命令手动同步。"""

    extension = models.OneToOneField(
        Extension, on_delete=models.CASCADE, primary_key=True, related_name="metric"
    )
    # 2026-09-30补上ROADMAP"性能优化"清单里明确列出的5个索引（之前一直
    # 标注"先不动，等上线前统一处理"，现在就是上线前，而且这次新增的
    # Category/Best Pages/Similar Extensions几个页面都要按user_count排序，
    # 不加索引这几个新页面在30万+行规模下会是全表扫描+排序）。只加在
    # ROADMAP原本列出的这5个字段上，不扩大范围。
    user_count = models.BigIntegerField(null=True, blank=True, db_index=True)
    user_count_change_1d = models.BigIntegerField(null=True, blank=True)
    user_count_change_7d = models.BigIntegerField(null=True, blank=True, db_index=True)
    user_count_change_30d = models.BigIntegerField(null=True, blank=True, db_index=True)
    user_growth_rate_1d = models.FloatField(null=True, blank=True)
    user_growth_rate_7d = models.FloatField(null=True, blank=True)
    user_growth_rate_30d = models.FloatField(null=True, blank=True, db_index=True)
    # 2026-09-30 新增（首发Detail页要求展示7D/30D/90D三档增长，之前只到30D）。
    # metrics.compute_metrics()本来就算这两个字段（Customer Dataset的29个
    # 指标列表里已经有user_count_change_90d/user_growth_rate_90d），只是
    # 网站这边ExtensionMetric之前没同步，这次补上。
    user_count_change_90d = models.BigIntegerField(null=True, blank=True)
    user_growth_rate_90d = models.FloatField(null=True, blank=True)
    # metrics.py 原生算出的"7日增长加速度"= 最近7日变化 - 前7日变化，
    # 正数代表在加速增长——Home页"增长加速"机会信号用的就是这个真实字段，
    # 不是我们自己另外发明的近似算法。
    user_count_growth_acceleration_7d = models.FloatField(null=True, blank=True, db_index=True)
    age_days = models.IntegerField(null=True, blank=True, db_index=True)
    days_since_update = models.IntegerField(null=True, blank=True)

    # 里程碑速度（2026-09-28 新增，支持Rankings"里程碑速度"组）——每个插件
    # 第一次达到某个用户量门槛用了多少天/是哪天。由 compute_milestones 命令
    # 批量算出来写进这里，不是靠 ExtensionChart.milestones（那个JSON字段
    # 现在只有少数手动 sync_extension 同步过的插件有数据，而且home()首页会
    # 把 chart__isnull=False 的全部插件读进Python内存处理，如果这里也写满
    # 30万+行会拖慢首页——这两个字段刻意分开存，互不影响）。
    # 语义：如果插件第一次出现在我们数据里时用户数已经超过这个门槛，说明
    # "首次达到"发生在数据范围之前，没法确定哪天，这两个字段保持NULL，不会
    # 瞎编一个日期——排序/筛选时这类插件自然被排除在"最快达到"榜单之外。
    days_to_1000 = models.IntegerField(null=True, blank=True)
    date_to_1000 = models.DateField(null=True, blank=True)
    days_to_3000 = models.IntegerField(null=True, blank=True)
    date_to_3000 = models.DateField(null=True, blank=True)
    days_to_10000 = models.IntegerField(null=True, blank=True)
    date_to_10000 = models.DateField(null=True, blank=True)
    days_to_30000 = models.IntegerField(null=True, blank=True)
    date_to_30000 = models.DateField(null=True, blank=True)
    days_to_100000 = models.IntegerField(null=True, blank=True)
    date_to_100000 = models.DateField(null=True, blank=True)

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "extension_metric"


class ExtensionChart(models.Model):
    """预计算好的历史曲线 + 首次里程碑，1插件1行。

    history: plugin_history.fetch_plugin_history() 的结果，原样存成
             [{date, user_count, rating_count, rating_value, extension_rank, overall_rank}, ...]
    milestones: plugin_history.compute_first_milestones() 的结果，原样存成
             [{milestone, first_reached_date, days_since_launch}, ...]
    """

    extension = models.OneToOneField(
        Extension, on_delete=models.CASCADE, primary_key=True, related_name="chart"
    )
    history = models.JSONField(default=list)
    milestones = models.JSONField(default=list)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "extension_chart"


class UserProfile(models.Model):
    """
    账号的"方案"信息。用 Django 自带的 User 模型（没有换成自定义User模型——
    现在换成本自定义模型要重置数据库，代价不值得；以后接谷歌登录时，
    django-allauth 直接挂在这个自带 User 模型上就行，不冲突）。

    真实 Stripe 接入前，plan 字段由后台（Django admin）手动设置来模拟不同
    方案效果，方便测试免费版行数限制这些逻辑是否真的随方案变化。
    """
    PLAN_FREE = "free"
    PLAN_PROFESSIONAL = "professional"
    PLAN_CUSTOM = "custom"
    PLAN_CHOICES = [
        (PLAN_FREE, "Free"),
        (PLAN_PROFESSIONAL, "Professional ($499/mo)"),
        (PLAN_CUSTOM, "Custom Intelligence ($1,499/mo)"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default=PLAN_FREE)
    # Stripe接入后：这两个字段记录Stripe那边的客户/订阅id，用来在webhook回调时
    # 找到对应的UserProfile、以及生成Billing Portal链接。plan字段仍然是
    # 权限判断唯一读取的字段（permissions.py不用改）——这两个只是"跟Stripe
    # 对账用的引用"，不是权限来源。
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")

    # 订阅周期信息（2026-09-29新增，支持Account页面展示套餐日期）——从Stripe
    # 的Subscription对象直接读出来存本地，不是自己算的。current_period_end
    # 双重含义：正常订阅中=下次扣款日期；已经点了取消(cancel_at_period_end=
    # True)但还没到期=服务到期日期，这两种情况Account页面要显示不同的文案，
    # 所以额外存了cancel_at_period_end这个标记，不能只看plan字段区分。
    subscription_start_date = models.DateField(null=True, blank=True)
    current_period_end = models.DateField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_profile"

    def __str__(self) -> str:
        return f"{self.user.email or self.user.username} ({self.plan})"


class ResearchRequest(models.Model):
    """定制情报版用户提交的研究需求。故意做得很简单（标题/描述/格式/附件），
    不是工单系统——原设计明确说了不要做复杂Ticket System。"""

    FORMAT_CHOICES = [("CSV", "CSV"), ("Excel", "Excel"), ("PDF", "PDF Report")]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="research_requests")
    title = models.CharField(max_length=255)
    description = models.TextField()
    output_format = models.CharField(max_length=32, choices=FORMAT_CHOICES, default="CSV")
    attachment = models.FileField(upload_to="research_requests/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "research_request"

    def __str__(self) -> str:
        return f"{self.title} ({self.user})"
