"""
legacy_bridge.py

对接你现有的 chrome_extension_data/scripts/ 离线计算代码
（snapshot_store.py / metrics.py / plugin_history.py）。

【重要】这里不复制、不修改那几个文件。它们只在你本机
chrome_extension_data/scripts/ 那一份是"真的"，通过 sys.path 直接从原地导入，
保证 Django 用的和你自己手动跑 python scripts/xxx.py 用的是同一份代码，
不会出现"仓库里存了一份、慢慢跟原版改出差异"的问题。

需要在 .env 里配置：
    LEGACY_SCRIPTS_DIR=/Users/你的用户名/Desktop/code/chrome_extension_data/scripts

这个目录本身、以及它同级的 parquet/ 目录，都不需要搬动或复制。
"""
from __future__ import annotations

import sys

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_path_ready = False


def _ensure_path() -> None:
    global _path_ready
    if _path_ready:
        return

    scripts_dir = getattr(settings, "LEGACY_SCRIPTS_DIR", "") or ""
    if not scripts_dir:
        raise ImproperlyConfigured(
            "LEGACY_SCRIPTS_DIR 没有配置。请在 .env 里设置它指向你本机 "
            "chrome_extension_data/scripts 的绝对路径，比如：\n"
            "LEGACY_SCRIPTS_DIR=/Users/你的用户名/Desktop/code/chrome_extension_data/scripts"
        )

    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    _path_ready = True


def get_snapshot_store():
    """返回原封不动的 snapshot_store 模块。"""
    _ensure_path()
    import snapshot_store
    return snapshot_store


def get_metrics():
    """返回原封不动的 metrics 模块。"""
    _ensure_path()
    import metrics
    return metrics


def get_plugin_history():
    """返回原封不动的 plugin_history 模块。"""
    _ensure_path()
    import plugin_history
    return plugin_history
