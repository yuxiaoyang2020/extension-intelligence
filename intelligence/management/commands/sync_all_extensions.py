"""
sync_all_extensions —— 全量同步：把最新一天真实存在的全部插件（排除主题/
其它类型，实测约30万个）的"当前状态+增长指标"批量写进MySQL。

跟 sync_top_growth / sync_showcase 的区别：那两个只挑一小批插件做demo
展示；这个是把 metrics.compute_metrics() 算出来的全部真插件都同步进去，
让 Rankings / Explorer / 站内搜索真正覆盖全量数据，不再是抽样。

只用到你本机已有的 parquet 文件（LEGACY_SCRIPTS_DIR 指向的那份），不需要
200多G的原始CSV——那是给以后"处理成卖给客户的CSV"完全不同的一条管线用的，
这条命令用不到，也不会碰它。

不做的事：不会给这30万个插件都同步历史曲线（ExtensionChart）。那个需要对
每个插件现场扫描一遍全部parquet，约1分钟/个，30万个insert根本跑不完，也
没必要——历史曲线只属于 Extension Detail 页面，按需用 sync_extension 单独
同步就行，这个设计从一开始就是这样，全量同步不改变这一点。

性能（这版改过一次）：最早用 Django 的 bulk_create/bulk_update 分两步做，
实测第二次全量重跑时几乎全部变成"更新"，bulk_update 比 bulk_create 慢了
一个数量级（bulk_update 内部对每一列拼一条 CASE WHEN id=... THEN ... 的
SQL，行数一多就很慢）。改成 MySQL 原生的 INSERT ... ON DUPLICATE KEY UPDATE，
一条SQL同时处理插入和更新，不区分这次主要是新建还是更新，速度稳定。

用法：
    python manage.py sync_all_extensions
    python manage.py sync_all_extensions --chunk-size 5000
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from intelligence.legacy_bridge import get_metrics, get_snapshot_store
from intelligence.models import Extension, ExtensionMetric
from intelligence.raw_csv_reader import load_extra_fields
from intelligence.sync_helpers import _raw_bulk_upsert, _to_bool, _to_date, _to_float, _to_int, _truncate

EXTENSION_FIELDS = [
    "extension_id", "name", "slug", "developer", "category", "payment_type",
    "rating_value", "rating_count", "version", "creation_date", "last_update",
    "extension_rank", "overall_rank",
    # 2026-09-29 新增（Explorer扩展第5步）：is_featured/is_trusted_publisher/
    # by_google，Explorer新增的三个勾选筛选要用。
    "is_featured", "is_trusted_publisher", "by_google",
    # 2026-09-30 新增（首发SEO Index Pool）：is_unlisted跟is_featured等三个
    # 同样来自metrics.compute_metrics()的25核心字段，不需要读原始CSV；
    # description不一样，是从当天raw_excel CSV额外合并进来的（见handle()里
    # 的_load_descriptions），原始CSV当天缺失时这一列会是空字符串，不影响
    # 其它字段正常同步。
    "is_unlisted", "description",
    "synced_at",
]
METRIC_FIELDS = [
    # 第一个必须是模型里真正的字段名——ExtensionMetric 的主键字段叫
    # "extension"（OneToOneField），不是 "extension_id"；"extension_id" 只是
    # Django 自动生成的列名/属性名(.attname)，_meta.get_field() 按字段名查找，
    # 传 "extension_id" 会报 FieldDoesNotExist。
    "extension", "user_count", "user_count_change_1d", "user_count_change_7d",
    "user_count_change_30d", "user_growth_rate_1d", "user_growth_rate_7d",
    "user_growth_rate_30d", "user_count_growth_acceleration_7d", "age_days",
    "days_since_update",
    # 2026-09-30 新增（首发Detail页要求展示7D/30D/90D三档增长）。
    "user_count_change_90d", "user_growth_rate_90d",
    "computed_at",
]


def _load_descriptions(stdout, date_str: str) -> dict:
    """读当天raw_excel CSV的description字段，返回 {id: description} 字典。

    这是"锦上添花"的字段（Detail页SEO内容用），不是核心同步字段——原始CSV
    当天缺失或读取出错时，只打印警告、返回空字典，绝不能让这一步的失败
    拖垮整个30万+插件的核心同步（user_count/rating等才是不能出错的部分）。
    """
    try:
        df = load_extra_fields(date_str, fields=["description"])
    except FileNotFoundError as exc:
        stdout.write(f"  [警告] 读取{date_str}的description失败，本次同步该字段全部留空: {exc}")
        return {}
    # 2026-09-30修复真正的根因（之前长期误判成rating_value的问题，其实
    # 一直是这里）：pandas读CSV时，就算指定了dtype=str，遇到空单元格依然
    # 会给出一个NaN（float类型），不是空字符串或None——这是dtype=str和
    # pandas缺失值处理各管一段导致的经典坑。下游`descriptions.get(id) or
    # ""`这个兜底会因此失效：NaN在Python里是"真值"（不是0，不算falsy），
    # `nan or ""`短路直接返回nan本身，根本轮不到""生效，NaN就这样被
    # 传进了不允许NULL的description列。在这里用fillna("")在源头堵掉，
    # 不依赖下游的or兜底。
    df["description"] = df["description"].fillna("")
    return dict(zip(df["id"], df["description"]))

# _raw_bulk_upsert 挪到了 sync_helpers.py（2026-09-28），因为新增的
# compute_milestones 命令也要用同一个通用批量upsert工具，不想两边各写一份。


class Command(BaseCommand):
    help = "全量同步：把最新一天全部真实插件（排除主题/其它类型）的当前状态+增长指标批量写进MySQL。"

    def add_arguments(self, parser):
        parser.add_argument("--chunk-size", type=int, default=5000, help="每批处理多少条，默认5000")

    def handle(self, *args, **options):
        chunk_size = options["chunk_size"]
        start_time = time.time()

        snapshot_store = get_snapshot_store()
        metrics = get_metrics()

        latest = snapshot_store.get_latest_date()
        self.stdout.write(f"最新快照日期: {latest}，开始计算全量增长指标（读取9天的parquet文件）...")
        df = metrics.compute_metrics(base_date=latest)
        self.stdout.write(f"计算完成，共 {len(df)} 条记录（含主题/其它类型）。")

        df = df[df["item_category"] == "extension"]
        total = len(df)
        self.stdout.write(f"按 item_category == 'extension' 过滤后，共 {total} 个真实插件。")

        if total == 0:
            self.stdout.write(self.style.WARNING("没有数据，退出。"))
            return

        descriptions = _load_descriptions(self.stdout, latest)
        self.stdout.write(f"读取到 {len(descriptions)} 个插件的description（当天原始CSV存在才有）。")

        rows = df.to_dict("records")
        num_batches = (total + chunk_size - 1) // chunk_size
        synced = 0

        for batch_idx in range(num_batches):
            batch = rows[batch_idx * chunk_size:(batch_idx + 1) * chunk_size]
            now = timezone.now()

            extensions = [
                Extension(
                    extension_id=row["id"],
                    name=_truncate(Extension, "name", row["name"]),
                    slug=(slugify(row["name"])[:255] or row["id"]),
                    developer=_truncate(Extension, "developer", row.get("author") or ""),
                    category=_truncate(Extension, "category", row.get("category") or ""),
                    payment_type=_truncate(Extension, "payment_type", row.get("payment_type") or ""),
                    rating_value=_to_float(row.get("rating_value")),
                    rating_count=_to_int(row.get("rating_count")),
                    version=_truncate(Extension, "version", row.get("version") or ""),
                    creation_date=_to_date(row.get("creation_date")),
                    last_update=_to_date(row.get("last_update")),
                    extension_rank=_to_int(row.get("extension_rank")),
                    overall_rank=_to_int(row.get("overall_rank")),
                    is_featured=_to_bool(row.get("is_featured")),
                    is_trusted_publisher=_to_bool(row.get("is_trusted_publisher")),
                    by_google=_to_bool(row.get("by_google")),
                    is_unlisted=_to_bool(row.get("is_unlisted")),
                    description=descriptions.get(row["id"]) or "",
                    synced_at=now,
                )
                for row in batch
            ]
            _raw_bulk_upsert(Extension, extensions, EXTENSION_FIELDS)

            metric_objs = [
                ExtensionMetric(
                    extension_id=row["id"],
                    user_count=_to_int(row.get("user_count")),
                    user_count_change_1d=_to_int(row.get("user_count_change_1d")),
                    user_count_change_7d=_to_int(row.get("user_count_change_7d")),
                    user_count_change_30d=_to_int(row.get("user_count_change_30d")),
                    user_growth_rate_1d=_to_float(row.get("user_growth_rate_1d")),
                    user_growth_rate_7d=_to_float(row.get("user_growth_rate_7d")),
                    user_growth_rate_30d=_to_float(row.get("user_growth_rate_30d")),
                    user_count_growth_acceleration_7d=_to_float(row.get("user_count_growth_acceleration_7d")),
                    age_days=_to_int(row.get("age_days")),
                    days_since_update=_to_int(row.get("days_since_update")),
                    user_count_change_90d=_to_int(row.get("user_count_change_90d")),
                    user_growth_rate_90d=_to_float(row.get("user_growth_rate_90d")),
                    computed_at=now,
                )
                for row in batch
            ]
            _raw_bulk_upsert(ExtensionMetric, metric_objs, METRIC_FIELDS)

            synced += len(batch)
            elapsed = time.time() - start_time
            self.stdout.write(
                f"[批次 {batch_idx + 1}/{num_batches}] 已同步 {synced}/{total} "
                f"({synced / total * 100:.1f}%) · 已耗时 {elapsed:.0f}秒"
            )

        elapsed = time.time() - start_time
        self.stdout.write(self.style.SUCCESS(f"全量同步完成，共 {synced} 个插件，总耗时 {elapsed:.0f}秒"))
