"""
首发Research/Insights的内容来源——2026-09-30新增。

刻意不做成数据库模型+后台录入系统：首发要求是"先准备结构，并至少支持
少量真正的数据研究内容，不要批量生成普通AI Blog"，重点是"少量、真实"，
不是"可规模化生产"，用一个Python常量列表足够，以后真的要做内容运营/
编辑后台了再升级成模型，现在不预先建那套复杂度。

下面这篇文章用的全部数字，都是2026-09-29你在本机对最新快照（2026-09-01，
257,411个真实Extension，排除is_unlisted=true）跑出来的真实统计结果，
不是编的——占比是在那组真实计数基础上现算的。2026-09-30全站转英文时，
这篇文章的标题/正文也翻译成了英文，但数字本身一个没动。
"""
from __future__ import annotations

ARTICLES = [
    {
        "slug": "how-many-chrome-extensions-have-real-traction",
        "title": "257,000+ Chrome Extensions: Only 3.7% Have Real, Sustained User Traction",
        "meta_description": "A statistical breakdown of user count, review count, and update-recency distribution across 257,411 real Chrome Extensions — how many actually reach meaningful market scale.",
        "published_date": "2026-09-29",
        "summary": "We analyzed all 257,411 Chrome Extensions in our platform (excluding delisted/unpublished extensions) across "
                   "user scale, review activity, and maintenance recency. The result: only about 3.7% of extensions meet all three "
                   "basic signals of \"real market presence\" at once — 1,000+ users, 10+ reviews, and an update within the last year.",
        "body": [
            "The total count of extensions in the Chrome Web Store is often reported as a single number, but that number hides a more "
            "important question: how many of these extensions are actually used, maintained, and growing? Using the most recent full "
            "snapshot on our platform (2026-09-01), we ran a cross-tabulation across all 257,411 real extensions (excluding anything where "
            "item_category isn't extension, and anything with is_unlisted=true, i.e. delisted/unpublished).",
            "**User scale is heavily right-skewed**: 75.0% of extensions (193,067) have between 0 and 99 users, while only 0.12% "
            "(302) have more than 1 million users. Only 7,833 extensions — 3.0% of the total — have 10,000+ users.",
            "**Review activity is similarly thin**: 45.98% of extensions (118,334) have at least one review, but only 8.26% "
            "(21,247) have 10 or more reviews, and just 1.67% (4,301) have 100 or more — meaning most extensions, even ones with users, "
            "leave behind almost no public feedback signal.",
            "**Recent update rate**: 170,216 extensions (66.1%) were updated within the past 365 days, and 127,739 "
            "(49.6%) within the past 180 days — meaning roughly a third of published extensions haven't seen a maintenance update in "
            "over a year.",
            "**Stacking all three signals together**: only 9,408 extensions — 3.66% of all 257,411 — meet all three conditions at "
            "once (user count ≥ 1,000, review count ≥ 10, updated within 365 days). If we raise the user threshold to 10,000 and drop the "
            "update-recency requirement, 15,395 extensions (5.98%) meet just the \"1,000+ users, 10+ reviews\" pair — about 6,000 more than "
            "the 9,408 that also meet the recency bar. That gap means over 6,000 extensions once reached meaningful user scale and review "
            "volume, but have since gone more than a year without an update.",
            "What this means for us: Extension Intelligence's first batch of search-engine-indexed Detail pages (the SEO Index Pool) "
            "uses exactly these 9,408 extensions as its starting candidate pool — not an arbitrary number, but the one subset in this "
            "distribution that simultaneously satisfies \"real user scale,\" \"real user feedback,\" and \"still actively maintained.\"",
        ],
    },
]


def get_all_articles():
    return ARTICLES


def get_article_by_slug(slug: str):
    for article in ARTICLES:
        if article["slug"] == slug:
            return article
    return None
