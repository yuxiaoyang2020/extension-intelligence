"""
sync_extension —— 按指定插件id手动同步，不是每日批处理。

用法：
    python manage.py sync_extension <插件id> [<插件id> ...]

调用现有 metrics.compute_metrics()（原样复用）算出最新一天的全量增长指标，
筛出你指定的插件，写入 MySQL；每个插件再现场调用一次 plugin_history 算历史
曲线（约1分钟/个，原样复用，不是bug是预期），存进 ExtensionChart。

如果报错 SnapshotNotFoundError：现有数据里已知有1天缺口(2026-02-07)，如果
base_date 计算窗口刚好覆盖到这一天就会报这个错，是数据本身的缺口。
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from intelligence.legacy_bridge import get_metrics, get_snapshot_store
from intelligence.sync_helpers import sync_rows


class Command(BaseCommand):
    help = "手动同步指定插件的当前状态+增长指标+历史曲线到MySQL。"

    def add_arguments(self, parser):
        parser.add_argument("extension_ids", nargs="+", help="要同步的插件 id（Chrome Web Store 扩展ID）")

    def handle(self, *args, **options):
        snapshot_store = get_snapshot_store()
        metrics = get_metrics()

        latest = snapshot_store.get_latest_date()
        self.stdout.write(f"最新快照日期: {latest}，开始计算全量增长指标（会读取9天的parquet文件）...")

        df = metrics.compute_metrics(base_date=latest)
        self.stdout.write(f"计算完成，共 {len(df)} 个插件，开始筛选目标插件...")

        wanted = set(options["extension_ids"])
        subset = df[df["id"].isin(wanted)]

        missing = wanted - set(subset["id"])
        if missing:
            self.stdout.write(self.style.WARNING(f"以下 id 在最新快照里没找到，跳过: {sorted(missing)}"))

        # item_category 有 extension/theme/application 三种真实取值（实测：
        # 306306/80013/14680）。网站定位是"Chrome Extension"数据集，主题和其它
        # 类型的条目不应该混进来，即使是手动指定id同步也一样过滤掉。
        not_extension = subset[subset["item_category"] != "extension"]
        if len(not_extension) > 0:
            self.stdout.write(self.style.WARNING(
                f"以下 id 的 item_category 不是 extension（不同步）: "
                f"{list(zip(not_extension['id'], not_extension['item_category']))}"
            ))
        subset = subset[subset["item_category"] == "extension"]

        synced = sync_rows(subset, self.stdout)
        self.stdout.write(self.style.SUCCESS(f"完成，共同步 {synced} 个插件"))
