"""
阿里云OSS客户端——只负责建连接，不做业务逻辑（跟 billing.py 封装Stripe SDK是
同样的组织方式）。

真正的上传逻辑在 management/commands/upload_customer_dataset.py。
"""
from __future__ import annotations

import oss2
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def get_oss_bucket() -> oss2.Bucket:
    if not (settings.OSS_ACCESS_KEY_ID and settings.OSS_ACCESS_KEY_SECRET
            and settings.OSS_ENDPOINT and settings.OSS_BUCKET_NAME):
        raise ImproperlyConfigured(
            "OSS 没配置完整，检查 .env 里的 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET / "
            "OSS_ENDPOINT / OSS_BUCKET_NAME"
        )
    auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
    endpoint = settings.OSS_ENDPOINT
    if not endpoint.startswith("http"):
        endpoint = f"https://{endpoint}"
    return oss2.Bucket(auth, endpoint, settings.OSS_BUCKET_NAME)
