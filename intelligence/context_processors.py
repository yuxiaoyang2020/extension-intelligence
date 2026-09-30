"""
全局模板变量——2026-09-30新增，专门给SEO/监控相关的少数几个设置值用，
不用每个view函数都手动传一遍。跟视图自己传的 page_type 不冲突：page_type
逐页不同，还是各view自己在context里设置，这里只提供有默认值时的兜底。
"""
from django.conf import settings


def seo_and_analytics(request):
    return {
        "google_site_verification": settings.GOOGLE_SITE_VERIFICATION,
        "posthog_api_key": settings.POSTHOG_API_KEY,
        "posthog_host": settings.POSTHOG_HOST,
    }
