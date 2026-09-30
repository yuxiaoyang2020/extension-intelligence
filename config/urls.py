from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from intelligence.sitemaps import sitemaps
from intelligence.views import health_check, robots_txt

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", health_check, name="health_check"),
    # 2026-09-30拆成两个独立sitemap文件（之前是一个/sitemap.xml合并两类页面）：
    # sitemap-core.xml只有固定页面，sitemap-extension-tier1.xml只收
    # seo_tier=tier1的Extension Detail页——不再无差别提交全部30万+插件。
    path("sitemap-core.xml", sitemap, {"sitemaps": {"core": sitemaps["core"]}}, name="sitemap_core"),
    path(
        "sitemap-extension-tier1.xml", sitemap,
        {"sitemaps": {"extension-tier1": sitemaps["extension-tier1"]}}, name="sitemap_extension_tier1",
    ),
    path("robots.txt", robots_txt, name="robots_txt"),
    # 只挂 socialaccount 这一部分（Google登录跳转+回调），不挂完整的
    # allauth.urls——那个会带上allauth自己的登录/注册/找回密码页面，
    # 跟我们 intelligence.urls 里已有的 accounts/login/ 等自定义页面冲突。
    # 具体路径是 accounts/google/login/ 和 accounts/google/login/callback/，
    # 跟我们已有的 accounts/login/、accounts/register/ 不会重名。
    #
    # 【踩坑记录】只 include allauth.socialaccount.urls 不够——实测这个版本
    # 的 allauth 不会自动发现装在 INSTALLED_APPS 里的 google provider 的
    # url（模板标签 {% provider_login_url %} 内部要 reverse "google_login"
    # 这个名字，找不到就报 NoReverseMatch）。google_login/google_callback
    # 这两个url名字实际定义在 provider 自己的 urls.py 里，需要显式再include
    # 一次，不能只靠自动发现。
    path("accounts/", include("allauth.socialaccount.urls")),
    path("accounts/", include("allauth.socialaccount.providers.google.urls")),
    path("", include("intelligence.urls")),
]

if settings.DEBUG:
    # 定制研究需求的附件走本地磁盘存储，开发环境需要这行才能直接访问上传的文件。
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
