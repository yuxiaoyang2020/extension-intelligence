"""
upload_customer_dataset —— 把本地已经生成好的 Customer Dataset CSV
（generate_customer_dataset 的产物）上传到阿里云OSS，客户直接从OSS下载，
不走咱们自己这台服务器（几十万行的CSV，量一大服务器带宽/CPU扛不住）。

命名规则（跟本地文件名一一对应，方便对照）：
    本地: {CUSTOMER_DATASET_DIR}/2026-09-01.csv
    OSS:  {OSS_CUSTOMER_DATASET_PREFIX}/2026-09-01.csv
    （默认前缀是 "customer-dataset"，完整object key就是 customer-dataset/2026-09-01.csv）

三档套餐（Free/Professional/Custom）不需要在OSS里分开存三份——55字段Schema
完全一样，区别只是"哪个客户能拿到哪段日期范围的下载链接"，那是发放下载权限
时的逻辑（不在这个命令的职责内），OSS这边永远是同一套按日期平铺的文件。

用大文件上传（oss2.resumable_upload）而不是普通的 put_object：文件大小一旦
超过阈值（默认10MB，咱们这些CSV基本都会超）就自动分片上传，网络中断了下次
重跑能从断点续传，不用重头再传一遍——几十万行的CSV在普通家庭宽带上传，中途
断线是大概率事件，这个不是可选的，是必须的。

用法：
    python manage.py upload_customer_dataset --date 2026-09-01   # 上传指定一天
    python manage.py upload_customer_dataset --all                # 上传本地全部CSV
    python manage.py upload_customer_dataset --all --force        # 忽略"OSS已有同名同大小文件就跳过"的检查，强制重传
"""
from __future__ import annotations

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

import oss2

from intelligence.oss_client import get_oss_bucket

# 新加坡OSS偶尔会有网络抖动（SSL连接被中断），单个文件最多自动重试这么
# 多次，重试间隔按指数退避（5/10/20/40秒）。
MAX_UPLOAD_RETRIES = 5
UPLOAD_RETRY_BACKOFF_SECONDS = 5


class Command(BaseCommand):
    help = "把本地 Customer Dataset CSV 上传到阿里云OSS，供客户直接下载。"

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default=None, help="只上传指定日期，格式 YYYY-MM-DD")
        parser.add_argument("--all", action="store_true", help="上传本地目录下全部CSV")
        parser.add_argument("--force", action="store_true", help="即使OSS上已有同名同大小的文件也强制重新上传")

    def handle(self, *args, **options):
        if not settings.CUSTOMER_DATASET_DIR:
            raise CommandError("CUSTOMER_DATASET_DIR 没配置，检查 .env")

        # 2026-09-28：generate_customer_dataset 扩到70字段之后，CSV在
        # {CUSTOMER_DATASET_DIR}/csv/ 子目录下（跟 parquet/ 子目录并列），
        # 不再是直接平铺在 CUSTOMER_DATASET_DIR 根目录。
        local_dir = Path(settings.CUSTOMER_DATASET_DIR) / "csv"

        if options["all"]:
            files = sorted(local_dir.glob("*.csv"))
        elif options["date"]:
            files = [local_dir / f"{options['date']}.csv"]
        else:
            raise CommandError("请指定 --date 2026-09-01 或者 --all")

        if not files:
            self.stdout.write(self.style.WARNING(f"{local_dir} 下没有找到CSV文件，先跑 generate_customer_dataset。"))
            return

        bucket = get_oss_bucket()
        prefix = settings.OSS_CUSTOMER_DATASET_PREFIX.rstrip("/")

        total = len(files)
        uploaded, skipped, failed = 0, 0, []

        for i, path in enumerate(files, start=1):
            if not path.exists():
                self.stdout.write(self.style.WARNING(f"[{i}/{total}] 跳过：本地文件不存在 {path}"))
                continue

            key = f"{prefix}/{path.name}"
            local_size = path.stat().st_size

            if not options["force"] and bucket.object_exists(key):
                meta = bucket.head_object(key)
                remote_size = int(meta.headers.get("Content-Length", -1))
                if remote_size == local_size:
                    self.stdout.write(f"[{i}/{total}] 跳过（OSS已有同名同大小文件）: {key}")
                    skipped += 1
                    continue

            def _progress(consumed_bytes, total_bytes, _key=key):
                pct = consumed_bytes / total_bytes * 100 if total_bytes else 0
                self.stdout.write(f"  上传中 {_key}: {pct:.0f}% ({consumed_bytes // 1024 // 1024}MB/{total_bytes // 1024 // 1024}MB)", ending="\r")

            # resumable_upload本身自带断点续传（分片+本地checkpoint），
            # 这里重试时不会从头重新传整个文件，只会接上次断掉的分片继续。
            success, last_exc = False, None
            for attempt in range(1, MAX_UPLOAD_RETRIES + 1):
                try:
                    oss2.resumable_upload(
                        bucket, key, str(path),
                        num_threads=4,
                        progress_callback=_progress,
                    )
                    success = True
                    break
                except (oss2.exceptions.RequestError, oss2.exceptions.ServerError) as exc:
                    last_exc = exc
                    self.stdout.write("")
                    if attempt < MAX_UPLOAD_RETRIES:
                        wait = UPLOAD_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                        self.stdout.write(self.style.WARNING(
                            f"[{i}/{total}] 网络中断（第{attempt}/{MAX_UPLOAD_RETRIES}次尝试），{wait}秒后自动重试: {key}"
                        ))
                        time.sleep(wait)

            self.stdout.write("")  # 换行，盖掉上面 \r 的进度行

            if not success:
                self.stdout.write(self.style.ERROR(
                    f"[{i}/{total}] 上传失败（已自动重试{MAX_UPLOAD_RETRIES}次仍失败，跳过继续下一个）: {key} —— {last_exc}"
                ))
                failed.append(key)
                continue

            self.stdout.write(self.style.SUCCESS(
                f"[{i}/{total}] 已上传: {key} ({local_size / 1024 / 1024:.1f} MB)"
            ))
            uploaded += 1

        self.stdout.write(self.style.SUCCESS(
            f"完成：上传 {uploaded} 个，跳过 {skipped} 个（已存在），失败 {len(failed)} 个，共 {total} 个文件。"
        ))
        if failed:
            self.stdout.write(self.style.ERROR("以下文件重试耗尽后仍失败，再跑一次 --all 会自动只重传这些（其它已成功的会被跳过）："))
            for key in failed:
                self.stdout.write(f"  - {key}")
