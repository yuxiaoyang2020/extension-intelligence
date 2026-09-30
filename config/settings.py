"""
Django 项目配置 —— Phase 1 最小骨架。

只保留跑通一个 Extension Detail 页面所需的配置，没有的东西（缓存、Celery、
第三方登录等）之后哪个阶段用得上再加，现在不预先塞进来。
"""
from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# 本地开发用 .env 文件（不提交进git，见 .gitignore），生产环境用真实环境变量覆盖。
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-for-production")
DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

# 上线用HTTPS前要打开的一组安全设置，默认全部关闭——本地开发环境用的是
# http://127.0.0.1，打开这些会直接导致登录状态/CSRF校验失效，所以不能跟着
# DEBUG=False自动打开（有的人上线前会先把DEBUG关掉测试，这时候还没配HTTPS
# 证书）。部署到真实域名、nginx配好HTTPS证书之后，在生产.env里把
# DJANGO_HTTPS_ENFORCED设成True、填好DJANGO_CSRF_TRUSTED_ORIGINS即可整体切换。
DJANGO_HTTPS_ENFORCED = os.environ.get("DJANGO_HTTPS_ENFORCED", "False") == "True"

# session/CSRF这两个cookie只允许通过HTTPS连接发送，防止被明文HTTP连接窃听。
SESSION_COOKIE_SECURE = DJANGO_HTTPS_ENFORCED
CSRF_COOKIE_SECURE = DJANGO_HTTPS_ENFORCED

# 收到http请求时强制跳转到https。
SECURE_SSL_REDIRECT = DJANGO_HTTPS_ENFORCED

# 生产环境用gunicorn+nginx：nginx终止TLS后转发给gunicorn的是普通http请求，
# Django自己看不出"这原本是一次https请求"——没有这一行，上面的
# SECURE_SSL_REDIRECT会死循环重定向（Django永远以为请求不安全，一直跳转）。
# 前提是nginx配置了转发X-Forwarded-Proto请求头，这两边必须配套，不是可选项。
if DJANGO_HTTPS_ENFORCED:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# 跨站请求伪造校验信任的来源——Django 5要求带完整scheme（https://域名），
# 不配的话生产环境登录/表单这些POST请求会被CSRF校验直接拒绝。在生产.env里
# 配置真实域名，比如：DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain.com
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# 首发SEO Index Pool（2026-09-30新增）——tier1插件因普通指标波动暂时掉出
# 门槛时，给多少天宽限期还继续index，不立刻noindex（避免频繁进出索引）。
# 做成可配置而不是硬编码，是你明确要求的，方便以后根据Search Console真实
# 数据调整，不用改代码重新部署。
SEO_TIER1_GRACE_DAYS = int(os.environ.get("SEO_TIER1_GRACE_DAYS", "30"))

# Google Search Console 域名验证——去GSC后台"其它验证方式"里选HTML标签，
# 复制content=""里面那串值填这里，不需要整个<meta>标签。留空就不输出这个
# 标签（本地开发/还没申请GSC之前不会出现在页面里）。
GOOGLE_SITE_VERIFICATION = os.environ.get("GOOGLE_SITE_VERIFICATION", "")

# PostHog——留空就不加载埋点脚本（本地开发默认不发真实事件，不用担心把
# 测试流量污染进生产项目的数据）。
POSTHOG_API_KEY = os.environ.get("POSTHOG_API_KEY", "")
POSTHOG_HOST = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")

# Sentry——留空就不初始化（见下面INSTALLED_APPS之后的初始化代码），本地
# 开发默认不上报，不会把调试时的报错也发到生产Sentry项目里。
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    # django-allauth 的硬依赖，即使不用它的多站点功能也必须装这个app。
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "intelligence",
]

SITE_ID = 1

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # django-allauth 要求的中间件（较新版本强制检查，没有这行会直接报错）。
    "allauth.account.middleware.AccountMiddleware",
]

# 我们自己的邮箱+密码登录（EmailAuthenticationForm）继续是唯一的"正规"入口，
# allauth 只用它的 Google 登录能力（socialaccount），不使用它自带的
# 注册/登录/找回密码页面——那些页面我们已经有自己的一套了，不重复做。
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# Google OAuth 客户端信息——去 Google Cloud Console 申请，填进 .env，
# 不要直接写在这个文件里（这个文件会被提交/分享，.env 不会）。
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
            "secret": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["profile", "email"],
    }
}
# 点"使用Google继续"直接跳转到Google，不用allauth自己的中间确认页。
SOCIALACCOUNT_LOGIN_ON_GET = True
# 按邮箱把Google登录关联到已有账号（见 intelligence/adapters.py 顶部注释，
# 不加这个的话，先注册再用同邮箱Google登录会变成两个不通的账号）。
SOCIALACCOUNT_ADAPTER = "intelligence.adapters.SocialAccountAdapter"

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "intelligence.context_processors.seo_and_analytics",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ.get("DB_NAME", "chrome_ext_intel"),
        "USER": os.environ.get("DB_USER", "cei"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "cei_dev_password"),
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DB_PORT", "3306"),
        "OPTIONS": {"charset": "utf8mb4"},
    }
}

# 生产环境安全校验（2026-09-29新增）——上面SECRET_KEY/DB_PASSWORD两处都写了
# "开发默认值"作为fallback，这份代码本身在git里公开着，绝对不能真的带着这两个
# 默认值上生产。只要DEBUG=False（生产模式）却检测到.env没有覆盖掉这两个默认值，
# 直接拒绝启动，不是等出安全事故才发现——比"写在ROADMAP清单里靠人记得检查"
# 可靠。DEBUG=True（本地开发）完全不受影响。
if not DEBUG:
    from django.core.exceptions import ImproperlyConfigured

    _insecure_env_vars = [
        name
        for name, is_still_default in {
            "DJANGO_SECRET_KEY": SECRET_KEY == "dev-only-not-for-production",
            "DB_PASSWORD": DATABASES["default"]["PASSWORD"] == "cei_dev_password",
        }.items()
        if is_still_default
    ]
    if _insecure_env_vars:
        raise ImproperlyConfigured(
            "DEBUG=False（生产模式）但以下环境变量还在用代码里写死的开发默认值，"
            f"必须在生产.env里设成真实值才能启动：{', '.join(_insecure_env_vars)}"
        )

AUTH_PASSWORD_VALIDATORS = []  # 先不加密码强度校验，保持注册流程极简

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# 定制研究需求表单的附件存本地磁盘（开发环境够用；生产环境要换成对象存储，
# 现在不需要，附件量小且是内部流程用，不是面向公众的大规模文件）。
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

LOGIN_URL = "intelligence:login"
LOGIN_REDIRECT_URL = "intelligence:account"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 现有 Metrics/Events/Rankings 脚本（snapshot_store.py / metrics.py / plugin_history.py）
# 所在的目录 —— 指向你本机 chrome_extension_data/scripts，不复制、不搬动。
# 必须在 .env 里配置，否则 intelligence.legacy_bridge 会在真正用到时报错提醒你。
LEGACY_SCRIPTS_DIR = os.environ.get("LEGACY_SCRIPTS_DIR", "")

# generate_customer_dataset 命令生成的、真正卖给客户的Customer Dataset存放
# 根目录（下面自动分 parquet/ 和 csv/ 两个子目录，各存70字段的Parquet/CSV）。
# 默认放在 chrome_extension_data/customer_dataset/（跟 scripts/、parquet/、
# raw_excel/ 同级），不占用Django项目自己的目录——这是数据产品，不是代码。
# 可以在 .env 里用 CUSTOMER_DATASET_DIR 单独覆盖这个路径。
CUSTOMER_DATASET_DIR = os.environ.get("CUSTOMER_DATASET_DIR") or (
    str(Path(LEGACY_SCRIPTS_DIR).parent / "customer_dataset") if LEGACY_SCRIPTS_DIR else ""
)

# Stripe——先在 Stripe 后台切到"测试模式"，用 sk_test_/pk_test_ 开头的key，
# 不会真的扣款。PRICE 是"Professional"/"Custom Intelligence"两个产品各自
# 建好的 Price ID（不是 Product ID，格式是 price_xxx）。
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_PROFESSIONAL = os.environ.get("STRIPE_PRICE_PROFESSIONAL", "")
STRIPE_PRICE_CUSTOM = os.environ.get("STRIPE_PRICE_CUSTOM", "")

# 阿里云OSS——Customer Dataset的CSV最终放这里，客户直接从OSS下载，不走
# 咱们自己这台小机器（几十万行的CSV批量下载会把服务器带宽/CPU占满）。
# Bucket 需要你自己在阿里云控制台建好；AccessKey 建议用RAM子账号单独建一个
# 只给这一个Bucket读写权限的，不要用主账号的AccessKey。
OSS_ACCESS_KEY_ID = os.environ.get("OSS_ACCESS_KEY_ID", "")
OSS_ACCESS_KEY_SECRET = os.environ.get("OSS_ACCESS_KEY_SECRET", "")
# Endpoint 是地域节点，比如杭州是 oss-cn-hangzhou.aliyuncs.com（去OSS控制台
# 你的Bucket概览页复制，不要带 https:// 前缀，oss2会自己拼）。
OSS_ENDPOINT = os.environ.get("OSS_ENDPOINT", "")
OSS_BUCKET_NAME = os.environ.get("OSS_BUCKET_NAME", "")
# OSS里存放CSV的"文件夹"前缀，object key 会是 {前缀}/{snapshot_date}.csv，
# 跟本地 CUSTOMER_DATASET_DIR 下的文件名一一对应，方便对照。
OSS_CUSTOMER_DATASET_PREFIX = os.environ.get("OSS_CUSTOMER_DATASET_PREFIX", "customer-dataset")

# Sentry错误监控（2026-09-30新增）——SENTRY_DSN留空就完全不初始化，本地
# 开发/沙盒测试不会意外把调试报错发到生产Sentry项目。放在settings.py最后
# 是因为sentry_sdk.init()需要读上面已经定义好的DEBUG来判断environment。
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        environment="development" if DEBUG else "production",
        # 先不采样性能追踪数据（traces_sample_rate=0），首发只要错误监控，
        # APM性能追踪属于P1/P2范围，等真实流量进来后再决定要不要开、开多少。
        traces_sample_rate=0,
        send_default_pii=False,
    )
