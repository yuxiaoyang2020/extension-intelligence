"""
sitemap-core.xml / sitemap-extension-tier1.xml —— 用 Django 自带的 sitemaps
框架，不用自己拼XML。2026-09-30拆成两个独立文件（之前是一个/sitemap.xml）：

    sitemap-core.xml            —— 固定的营销/功能/内容页面
    sitemap-extension-tier1.xml —— 只收 seo_tier=tier1 的Extension Detail页

【重要变更，2026-09-30】：Extension sitemap以前是 Extension.objects.all()，
把MySQL里全部30万+插件（含未过滤的is_unlisted=true）无差别提交给Google，
这是新站上线前必须先修掉的问题——现在改成只提交真正通过SEO Index Pool
（Candidate Pool数字门槛 + Quality Gate内容质量门槛，见 compute_seo_tier
命令）、状态是tier1的插件。tier1_grace故意不收进sitemap（页面仍然index，
只是不再主动催Google优先重新抓取，见ROADMAP里"sitemap语义"那段讨论）；
candidate/excluded更不用说，两者都是当前不主动追加索引的状态。

不收录：登录/注册/账户/内部管理这些页面（见 views.py 里对应页面的
noindex 设置，这两处要保持一致：不该被收录的页面，sitemap 里也不该出现）；
Rankings/Explorer的query parameter变体也不收录（本身就是noindex）。
"""
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Extension


class StaticViewSitemap(Sitemap):
    """固定页面，改动频率低，用一个固定的 changefreq/priority 就够了。"""

    changefreq = "daily"

    def items(self):
        return [
            "intelligence:home", "intelligence:dataset", "intelligence:rankings",
            "intelligence:explorer", "intelligence:pricing", "intelligence:methodology",
            "intelligence:data_dictionary", "intelligence:about", "intelligence:contact",
            "intelligence:category_productivity", "intelligence:best_productivity",
            "intelligence:research_index",
        ]

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return 1.0 if item == "intelligence:home" else 0.7


class ExtensionTier1Sitemap(Sitemap):
    """只收 seo_tier=tier1 的Extension Detail页——不含tier1_grace（见文件头
    注释的sitemap语义讨论）。

    lastmod 用 seo_tier_since 而不是 synced_at：synced_at 是 auto_now，
    每天全量同步都会更新，导致lastmod对全部30万+行"无脑每天刷新"，起不到
    "反映真实重要内容变化"的作用；seo_tier_since 只在真正进入/离开tier1时
    才变，是这份代码目前能拿到的、最接近"真实重要变化"的时间戳。"""

    changefreq = "weekly"
    priority = 0.5

    def items(self):
        # 显式order_by：Django的sitemap分页(单份sitemap超过50000条URL才会
        # 触发)依赖稳定排序才能正确分页，不加会有UnorderedObjectListWarning。
        return Extension.objects.filter(seo_tier=Extension.SEO_TIER_TIER1).only(
            "extension_id", "slug", "seo_tier_since"
        ).order_by("extension_id")

    def location(self, obj):
        return reverse("intelligence:extension_detail", args=[obj.extension_id, obj.slug])

    def lastmod(self, obj):
        return obj.seo_tier_since


sitemaps = {
    "core": StaticViewSitemap,
    "extension-tier1": ExtensionTier1Sitemap,
}
