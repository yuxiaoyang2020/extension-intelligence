"""
compute_seo_tier —— 首发SEO Index Pool的核心：每天判断每个插件的
Extension.seo_tier该不该变化，Detail页的robots meta和sitemap成员资格都
只读这个字段，不在请求路径上现算。

═══════════════════════════════════════════════════════════════════
一、Candidate Pool 基础门槛（数字条件，见 _meets_base_criteria）
═══════════════════════════════════════════════════════════════════
    item_category = extension   （Extension表本身已经只有这个类型，天然满足）
    is_unlisted != true
    user_count >= 1000
    rating_count >= 10
    days_since_update <= 365

═══════════════════════════════════════════════════════════════════
二、Quality Gate（内容质量门槛，见 _passes_quality_gate）——满足上面的数字
   条件不代表自动index，还要通过这几项，都是有明确依据的技术判断，没有
   凭空发明"description至少几百字"这类无依据规则：
═══════════════════════════════════════════════════════════════════
    - name 非空
    - description 非空，且不是跟name完全相同的占位内容
    - category 非空
    - 核心数据正常：user_count非负；rating_value（如果有）在0-5之间
    - 至少30个不同日期的历史数据——用 age_days>=29 作为技术代理指标
      （age_days = 这个插件第一次出现在我们数据里到今天的自然日天数，在
      daily_sync连续跑、没有断档的前提下，这个数字本身就约等于"有多少个
      不同日期的历史数据点"。如果你们的同步曾经断档过，这个代理指标会
      偏乐观，需要你知晓这个假设）
    - 7D/30D/90D 至少一个能可靠算出增长指标——用 user_count_change_7d/
      30d/90d 至少一个非NULL判断（metrics.py本身在历史窗口不够时就会让
      这些字段保持NULL，"非NULL"本来就是metrics.py自己对"可靠"的定义，
      不是我们另外发明的标准）

═══════════════════════════════════════════════════════════════════
三、状态机（Extension.seo_tier）
═══════════════════════════════════════════════════════════════════
    candidate:
        今天同时满足Pool门槛+Quality Gate → 累计连续达标天数（
        seo_qualify_streak_start没设就设成今天，设了就算天数差）；
        连续满7天 → 升级tier1，记seo_tier_since=今天
        任何一天不满足 → 重置seo_qualify_streak_start为空，重新计数

    tier1:
        继续满足 → 不动
        is_unlisted=true 或核心数据出现明显异常（负数/评分越界/name为空）
            → 直接excluded（"严重"信号，不给宽限期）
        普通门槛/质量条件的其它部分不满足（比如user_count跌破1000、
        rating_count跌破10、days_since_update超过365、增长指标暂时算不出
        来）→ 转tier1_grace，记seo_tier_since=今天（宽限期开始）

    tier1_grace:
        恢复满足 → 回到tier1，记seo_tier_since=今天
        is_unlisted=true 或严重数据异常 → 直接excluded（不等宽限期用完）
        持续不满足超过 SEO_TIER1_GRACE_DAYS（默认30天，可在.env配置）
            → excluded

    excluded:
        重新同时满足Pool门槛+Quality Gate → 回到candidate重新走一遍7天流程
        （不直接跳回tier1——excluded是相对严重的状态，"重新证明"比"直接
        恢复"更安全，这是我的默认选择，你可以要求改）
        否则 → 不动

═══════════════════════════════════════════════════════════════════
四、首次上线冷启动（"连续7天"用已有历史直接回算，不用重新等7天）
═══════════════════════════════════════════════════════════════════
    只在真正第一次跑这个命令时触发（整张Extension表seo_tier_since/
    seo_qualify_streak_start全部是NULL，说明这几个字段是刚加上的新字段）：

    对"今天"已经同时满足Pool门槛+Quality Gate的插件，额外用
    snapshot_store里最近7个快照日期，逐天重新跑一次
    metrics.compute_metrics(base_date=该日)，只检查会逐日波动的那几项
    数字门槛（user_count/rating_count/days_since_update/age_days/
    是否有7D30D90D增长指标）——name/description/category这些插件级静态
    属性不会逐日波动，用"今天"检查一次就够，不需要为了追求形式上的
    "连续7天"而重复读取7天的原始CSV。

    如果最近7个快照日期全部满足 → 直接跳过candidate排队，当天就是tier1
    （这不是凭空给面子，是我们本来就有这7天的真实历史数据，只是没有
    "seo_tier这个字段"去记录它，现在把它回算出来而已，语义上不算是
    "临时破格"）
    如果不是全部满足 → 按普通candidate流程从今天开始重新计数

用法：
    python manage.py compute_seo_tier
"""
from __future__ import annotations

import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from intelligence.legacy_bridge import get_metrics, get_snapshot_store
from intelligence.models import Extension
from intelligence.sync_helpers import _raw_bulk_update

ENTRY_STREAK_DAYS = 7

EXTENSION_SEO_FIELDS = ["extension_id", "seo_tier", "seo_tier_since", "seo_qualify_streak_start"]


def _meets_base_criteria(ext) -> bool:
    metric = getattr(ext, "metric", None)
    if ext.is_unlisted is True:
        return False
    if metric is None or metric.user_count is None or metric.user_count < 1000:
        return False
    if ext.rating_count is None or ext.rating_count < 10:
        return False
    if metric.days_since_update is None or metric.days_since_update > 365:
        return False
    return True


def _passes_quality_gate(ext) -> bool:
    metric = getattr(ext, "metric", None)
    if not ext.name or not ext.name.strip():
        return False
    description = (ext.description or "").strip()
    if not description or description.lower() == ext.name.strip().lower():
        return False
    if not ext.category or not ext.category.strip():
        return False
    if metric is None or metric.user_count is None or metric.user_count < 0:
        return False
    if ext.rating_value is not None and not (0 <= ext.rating_value <= 5):
        return False
    if metric is None or metric.age_days is None or metric.age_days < 29:
        return False
    has_growth_signal = metric is not None and any(
        v is not None
        for v in (metric.user_count_change_7d, metric.user_count_change_30d, metric.user_count_change_90d)
    )
    if not has_growth_signal:
        return False
    return True


def _is_severe(ext) -> bool:
    """跟_passes_quality_gate/_meets_base_criteria里"普通不满足"的区别：
    这几种情况被认为是"明确的下架/数据损坏信号"，不给tier1_grace的宽限期，
    直接excluded——is_unlisted是你原话明确点名的；核心数据出现负数/评分
    越界/name为空这几种，判断标准是"这已经不是'指标暂时低一点'，而是数据
    本身有问题"，不是我随意扩大"严重"的范围。"""
    metric = getattr(ext, "metric", None)
    if ext.is_unlisted is True:
        return True
    if not ext.name or not ext.name.strip():
        return True
    if metric is not None and metric.user_count is not None and metric.user_count < 0:
        return True
    if ext.rating_value is not None and not (0 <= ext.rating_value <= 5):
        return True
    return False


class Command(BaseCommand):
    help = "计算首发SEO Index Pool的候选/Tier1/宽限期/排除状态（Extension.seo_tier）。"

    def handle(self, *args, **options):
        start_time = time.time()
        today = timezone.now().date()
        grace_days = settings.SEO_TIER1_GRACE_DAYS

        extensions = list(Extension.objects.select_related("metric").all())
        self.stdout.write(f"共 {len(extensions)} 个插件，开始计算SEO分层状态（宽限期 {grace_days} 天）...")

        is_first_run = not Extension.objects.exclude(seo_tier_since__isnull=True).exists() and not \
            Extension.objects.exclude(seo_qualify_streak_start__isnull=True).exists()
        bootstrap_pass_ids = set()
        if is_first_run:
            bootstrap_pass_ids = self._bootstrap_last_7_days(extensions)

        counts = {
            "candidate_to_tier1": 0, "candidate_streak_reset": 0, "candidate_streak_progress": 0,
            "tier1_stay": 0, "tier1_to_grace": 0, "tier1_to_excluded": 0,
            "grace_to_tier1": 0, "grace_to_excluded": 0, "grace_stay": 0,
            "excluded_to_candidate": 0, "excluded_stay": 0,
        }
        rejection_reasons = {"empty_name": 0, "invalid_description": 0, "empty_category": 0,
                              "invalid_core_data": 0, "insufficient_history": 0, "no_growth_signal": 0}

        to_update = []
        for ext in extensions:
            base_ok = _meets_base_criteria(ext)
            quality_ok = _passes_quality_gate(ext) if base_ok else False
            qualifies = base_ok and quality_ok
            severe = _is_severe(ext)

            if not qualifies and base_ok:
                # 只在数字条件已经过关、卡在quality gate的情况下细分原因，
                # 方便统计"各Quality Gate淘汰数量"（数字条件都没过的，不算
                # 进quality gate淘汰统计，那是Candidate Pool门槛本身淘汰的）。
                self._tally_rejection_reason(ext, rejection_reasons)

            tier = ext.seo_tier
            if tier == Extension.SEO_TIER_CANDIDATE:
                if qualifies:
                    if ext.extension_id in bootstrap_pass_ids:
                        ext.seo_tier = Extension.SEO_TIER_TIER1
                        ext.seo_tier_since = today
                        ext.seo_qualify_streak_start = None
                        counts["candidate_to_tier1"] += 1
                    else:
                        streak_start = ext.seo_qualify_streak_start or today
                        streak_days = (today - streak_start).days + 1
                        ext.seo_qualify_streak_start = streak_start
                        if streak_days >= ENTRY_STREAK_DAYS:
                            ext.seo_tier = Extension.SEO_TIER_TIER1
                            ext.seo_tier_since = today
                            ext.seo_qualify_streak_start = None
                            counts["candidate_to_tier1"] += 1
                        else:
                            counts["candidate_streak_progress"] += 1
                else:
                    if ext.seo_qualify_streak_start is not None:
                        ext.seo_qualify_streak_start = None
                        counts["candidate_streak_reset"] += 1

            elif tier == Extension.SEO_TIER_TIER1:
                if qualifies:
                    counts["tier1_stay"] += 1
                elif severe:
                    ext.seo_tier = Extension.SEO_TIER_EXCLUDED
                    ext.seo_tier_since = today
                    counts["tier1_to_excluded"] += 1
                else:
                    ext.seo_tier = Extension.SEO_TIER_TIER1_GRACE
                    ext.seo_tier_since = today
                    counts["tier1_to_grace"] += 1

            elif tier == Extension.SEO_TIER_TIER1_GRACE:
                if qualifies:
                    ext.seo_tier = Extension.SEO_TIER_TIER1
                    ext.seo_tier_since = today
                    counts["grace_to_tier1"] += 1
                elif severe:
                    ext.seo_tier = Extension.SEO_TIER_EXCLUDED
                    ext.seo_tier_since = today
                    counts["grace_to_excluded"] += 1
                else:
                    grace_start = ext.seo_tier_since or today
                    if (today - grace_start).days >= grace_days:
                        ext.seo_tier = Extension.SEO_TIER_EXCLUDED
                        ext.seo_tier_since = today
                        counts["grace_to_excluded"] += 1
                    else:
                        counts["grace_stay"] += 1

            elif tier == Extension.SEO_TIER_EXCLUDED:
                if qualifies:
                    ext.seo_tier = Extension.SEO_TIER_CANDIDATE
                    ext.seo_qualify_streak_start = today
                    ext.seo_tier_since = None
                    counts["excluded_to_candidate"] += 1
                else:
                    counts["excluded_stay"] += 1

            to_update.append(ext)

        # 用_raw_bulk_update（纯UPDATE）而不是_raw_bulk_upsert：这里的所有
        # 行都是从Extension.objects.all()查出来的已有行，不需要"顺便插入
        # 不存在的行"这个upsert语义；用upsert反而会因为EXTENSION_SEO_FIELDS
        # 只有4列，触发MySQL对"候选插入行"的NOT NULL校验（name/slug等字段
        # 没有数据库级默认值），详见sync_helpers._raw_bulk_update的文档字符串。
        _raw_bulk_update(Extension, to_update, EXTENSION_SEO_FIELDS)

        elapsed = time.time() - start_time
        self.stdout.write(self.style.SUCCESS(f"完成，总耗时 {elapsed:.0f}秒"))
        self.stdout.write("")
        self.stdout.write("=== 状态转换汇总 ===")
        for k, v in counts.items():
            if v:
                self.stdout.write(f"  {k}: {v}")
        self.stdout.write("")
        self.stdout.write("=== Quality Gate淘汰原因分布（仅统计已过数字门槛、卡在质量门槛的） ===")
        for k, v in rejection_reasons.items():
            if v:
                self.stdout.write(f"  {k}: {v}")
        self.stdout.write("")
        final_counts = {}
        for choice_value, _ in Extension.SEO_TIER_CHOICES:
            final_counts[choice_value] = sum(1 for e in to_update if e.seo_tier == choice_value)
        self.stdout.write("=== 最终各状态计数 ===")
        for k, v in final_counts.items():
            self.stdout.write(f"  {k}: {v}")

    def _tally_rejection_reason(self, ext, rejection_reasons):
        metric = getattr(ext, "metric", None)
        if not ext.name or not ext.name.strip():
            rejection_reasons["empty_name"] += 1
            return
        description = (ext.description or "").strip()
        if not description or description.lower() == ext.name.strip().lower():
            rejection_reasons["invalid_description"] += 1
            return
        if not ext.category or not ext.category.strip():
            rejection_reasons["empty_category"] += 1
            return
        if metric is None or metric.user_count is None or metric.user_count < 0 or (
            ext.rating_value is not None and not (0 <= ext.rating_value <= 5)
        ):
            rejection_reasons["invalid_core_data"] += 1
            return
        if metric is None or metric.age_days is None or metric.age_days < 29:
            rejection_reasons["insufficient_history"] += 1
            return
        rejection_reasons["no_growth_signal"] += 1

    def _bootstrap_last_7_days(self, extensions) -> set:
        """首次上线冷启动：对today已经满足Pool门槛+Quality Gate的插件，额外
        逐天回算最近7个快照日期的Pool数字门槛（不含quality gate的静态属性
        检查，见文件头注释的理由），全部满足才直接判定为bootstrap通过。"""
        self.stdout.write("检测到这是首次运行（seo_tier字段全表为空），执行7天历史回算冷启动...")

        candidates_today = {
            e.extension_id: e for e in extensions
            if _meets_base_criteria(e) and _passes_quality_gate(e)
        }
        if not candidates_today:
            return set()

        snapshot_store = get_snapshot_store()
        metrics = get_metrics()
        dates = snapshot_store.list_dates()[-ENTRY_STREAK_DAYS:]
        if len(dates) < ENTRY_STREAK_DAYS:
            self.stdout.write(
                self.style.WARNING(f"  快照日期只有{len(dates)}天（不足{ENTRY_STREAK_DAYS}天），跳过冷启动，全部走正常7天排队流程。")
            )
            return set()

        pass_count = {ext_id: 0 for ext_id in candidates_today}
        for d in dates:
            df = metrics.compute_metrics(base_date=d)
            df = df[df["item_category"] == "extension"]
            df = df[df["id"].isin(candidates_today.keys())]
            for _, row in df.iterrows():
                user_count = row.get("user_count")
                rating_count = row.get("rating_count")
                days_since_update = row.get("days_since_update")
                age_days = row.get("age_days")
                has_growth = any(
                    row.get(c) is not None and row.get(c) == row.get(c)  # 排除NaN（NaN != NaN）
                    for c in ("user_count_change_7d", "user_count_change_30d", "user_count_change_90d")
                )
                is_unlisted = str(row.get("is_unlisted")).strip().lower() == "true"
                ok = (
                    not is_unlisted
                    and user_count is not None and user_count == user_count and user_count >= 1000
                    and rating_count is not None and rating_count == rating_count and rating_count >= 10
                    and days_since_update is not None and days_since_update == days_since_update and days_since_update <= 365
                    and age_days is not None and age_days == age_days and age_days >= 29
                    and has_growth
                )
                if ok:
                    pass_count[row["id"]] += 1

        bootstrap_pass_ids = {ext_id for ext_id, c in pass_count.items() if c >= ENTRY_STREAK_DAYS}
        self.stdout.write(
            f"  冷启动回算完成：{len(candidates_today)} 个当天已达标的插件中，"
            f"{len(bootstrap_pass_ids)} 个最近{ENTRY_STREAK_DAYS}天全部达标，直接进tier1；"
            f"其余 {len(candidates_today) - len(bootstrap_pass_ids)} 个从今天开始正常排队。"
        )
        return bootstrap_pass_ids
