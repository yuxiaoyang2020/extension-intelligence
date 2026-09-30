"""
daily_sync —— 每日全套同步任务的统一入口，替代手动依次敲好几条命令。

不预先锁定用crontab还是Celery beat调度——不管以后选哪个，都只是"定时调用
这一个命令"，命令内部的编排逻辑不用跟着调度方式改，方案定了直接把
`python manage.py daily_sync` 这一条接进去就行。

按顺序跑5步：

    网站/MySQL这条链（有依赖关系，顺序不能乱）：
    1. sync_all_extensions      —— 全量刷新Extension/ExtensionMetric当前状态
                                    （2026-09-30起还包括is_unlisted/description/
                                    90D增长这几个SEO用得到的新字段）
    2. compute_seo_tier         —— 必须排在sync_all_extensions之后：读它刚
                                    同步好的数据算SEO Index Pool分层状态
                                    （candidate/tier1/tier1_grace/excluded），
                                    Detail页robots meta和sitemap都靠这个字段
    3. compute_milestones       —— 里程碑速度全量重扫（不是增量算法，见该命令
                                    文件头注释；随着历史变长耗时会缓慢增加，
                                    现在365天约67秒，可以用 --skip-milestones
                                    先跳过，以后改成每周跑一次也不影响正确性）

    对外销售数据这条链（跟上面互相独立）：
    4. generate_customer_dataset —— 不传日期=只生成最新一天的70字段Parquet+CSV
    5. upload_customer_dataset   —— 把第4步生成的当天CSV传到OSS（OSS没配置
                                    好之前用 --skip-upload 跳过，等你阿里云
                                    那边配好Bucket/RAM子账号再打开）

任何一步失败不会中断后面几步（各自try/except），最后打印汇总表哪几步
成功/失败/跳过；只要有一步真正失败，最终用非0退出码结束进程——crontab的
邮件通知、systemd的失败告警这些都是靠退出码判断的，不能让某一步偷偷坏掉
但整个命令看起来"跑完了"。

用法：
    python manage.py daily_sync
    python manage.py daily_sync --skip-upload                 # OSS还没配置好
    python manage.py daily_sync --skip-milestones              # 想省这步耗时
    python manage.py daily_sync --skip-sync --skip-milestones  # 只跑Customer Dataset那条链
"""
from __future__ import annotations

import sys

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "每日同步统一入口：sync_all_extensions → compute_seo_tier → compute_milestones → "
        "generate_customer_dataset → upload_customer_dataset"
    )

    def add_arguments(self, parser):
        parser.add_argument("--skip-sync", action="store_true", help="跳过 sync_all_extensions")
        parser.add_argument("--skip-seo-tier", action="store_true", help="跳过 compute_seo_tier")
        parser.add_argument("--skip-milestones", action="store_true", help="跳过 compute_milestones")
        parser.add_argument("--skip-customer-dataset", action="store_true", help="跳过 generate_customer_dataset")
        parser.add_argument("--skip-upload", action="store_true", help="跳过 upload_customer_dataset（比如OSS还没配置好）")

    def handle(self, *args, **options):
        results = []

        def run_step(name, skip, func):
            if skip:
                self.stdout.write(self.style.WARNING(f"[跳过] {name}"))
                results.append((name, "跳过"))
                return
            self.stdout.write(self.style.MIGRATE_HEADING(f"[开始] {name}"))
            try:
                func()
                self.stdout.write(self.style.SUCCESS(f"[完成] {name}"))
                results.append((name, "成功"))
            except Exception as exc:  # noqa: BLE001 —— 故意宽泛捕获，单步失败不能拖垮其它步骤
                self.stderr.write(self.style.ERROR(f"[失败] {name}: {exc}"))
                results.append((name, f"失败: {exc}"))

        run_step("sync_all_extensions", options["skip_sync"], lambda: call_command("sync_all_extensions"))
        run_step("compute_seo_tier", options["skip_seo_tier"], lambda: call_command("compute_seo_tier"))
        run_step("compute_milestones", options["skip_milestones"], lambda: call_command("compute_milestones"))
        run_step("generate_customer_dataset", options["skip_customer_dataset"], lambda: call_command("generate_customer_dataset"))
        run_step("upload_customer_dataset", options["skip_upload"], lambda: call_command("upload_customer_dataset"))

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=== daily_sync 汇总 ==="))
        any_failed = False
        for name, outcome in results:
            self.stdout.write(f"  {name}: {outcome}")
            if outcome.startswith("失败"):
                any_failed = True

        if any_failed:
            self.stderr.write(self.style.ERROR("daily_sync 有步骤失败，退出码非0（供crontab/监控识别）"))
            sys.exit(1)
