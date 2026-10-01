"""
共享的同步逻辑，被 sync_extension / sync_top_growth / sync_all_extensions /
compute_milestones 这几个命令复用，避免各自写一份一样的写库代码。
"""
from __future__ import annotations

import json

import pandas as pd
from django.db import connection
from django.utils.text import slugify

from .legacy_bridge import get_plugin_history
from .models import Extension, ExtensionChart, ExtensionMetric


def _to_int(value):
    return None if pd.isna(value) else int(value)


def _to_float(value):
    """2026-09-30修复：原始数据里偶尔会出现字符串"nan"这种文本形式的缺失值
    标记（不是真正的浮点NaN/None），pd.isna()识别不出字符串"nan"是缺失值
    （它只认识真正的NaN/None/NaT这些），于是继续走到float(value)——而
    Python的float("nan")会"成功"转成一个真正的NaN浮点数，这个NaN混过了
    检查一路传到MySQL那一层，PyMySQL的escape_float()才终于报错拒绝写入
    （2026-09-30 Windows真实数据触发，Mac那批数据没有这行边界情况）。
    加一步转换后校验：float("nan")转出来的结果用`result != result`
    （NaN是唯一一个"不等于自己"的浮点数，比额外import math.isnan更简洁）
    再拦一次，双重保险。"""
    if pd.isna(value):
        return None
    result = float(value)
    return None if result != result else result


def _to_date(value):
    ts = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(ts) else ts.date()


def _to_bool(value):
    """把parquet里存成字符串"True"/"False"的布尔字段（is_featured/
    is_trusted_publisher/by_google）转成Python bool——大小写不敏感。
    空值/无法识别的字符串返回None，不是False："没有这个数据"和"明确是
    False"是两码事，不能混为一谈（跟events.py的_normalize_bool_str是
    同样的字段格式，但那边是做"是否变化"比较，这里是转成真正的布尔值存
    进MySQL，用途不同，没有直接复用那个函数）。"""
    if pd.isna(value):
        return None
    s = str(value).strip().lower()
    if s == "true":
        return True
    if s == "false":
        return False
    return None


def _truncate(model, field_name, value):
    """按模型字段实际的 max_length 硬截断字符串——防止真实数据里偶尔出现的
    异常长字段（实测 version 字段撞到过 "Data too long" 报错）中断整批同步。
    截断只影响这一个字段展示，不影响其它字段/其它行，比让整个 bulk_create
    直接报错、卡在半路强得多。"""
    if not value:
        return value
    max_length = model._meta.get_field(field_name).max_length
    return value[:max_length] if max_length and len(value) > max_length else value


def _sanitize_for_mysql(value):
    """最后一道防线——不管NaN是从哪个上游字段/哪种奇怪的原始数据格式漏进来
    的（2026-09-30已经在_to_float()里堵过一次字符串"nan"这个来源，但
    Windows真实数据上同样的报错还在复现，说明还有别的漏网来源，一时半会
    排查不出具体是谁），统一在真正拼SQL之前这个唯一的关卡上兜底转成
    None——是float且不等于自身（NaN的定义性特征）就转None，其它值原样
    传过去。这样不用穷举每一个可能产生NaN的上游字段，只要最终传到这里的
    是NaN，都会被拦下来，从根上保证不会有NaN传到MySQL导致整批全部失败。
    调试用：拦下来的时候打印一下原始值，方便回头定位到底是哪来的。
    """
    if isinstance(value, float) and value != value:
        import sys
        print(f"  [调试] 拦截到一个NaN值，已转成NULL（不会中断同步）", file=sys.stderr)
        return None
    return value


def _raw_bulk_upsert(model, objs, field_names):
    """
    用 MySQL 原生的 INSERT ... ON DUPLICATE KEY UPDATE 一次性处理一整批的
    插入/更新——比 Django 的 bulk_create()+bulk_update() 分两步快得多（尤其
    是 bulk_update，实测慢了一个数量级），也不用先查一遍"哪些id已经存在"。

    field_names 第一个必须是主键字段（Extension 是 extension_id 本身；
    ExtensionMetric 是 OneToOneField "extension"，主键列名是 extension_id）。

    只会更新 field_names 里列出的这些列——没列出的列（比如只想更新
    milestone相关字段时，不会碰 user_count 等已有字段）保持原值不变，
    这是 MySQL ON DUPLICATE KEY UPDATE 本身的行为，不用额外处理。
    """
    table = model._meta.db_table
    columns = [model._meta.get_field(f).column for f in field_names]
    attnames = [model._meta.get_field(f).attname for f in field_names]

    col_list = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    # 第一列是主键，不需要出现在 UPDATE 子句里
    update_clause = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in columns[1:])
    sql = f"INSERT INTO `{table}` ({col_list}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_clause}"

    rows = [tuple(_sanitize_for_mysql(getattr(obj, a)) for a in attnames) for obj in objs]
    with connection.cursor() as cursor:
        cursor.executemany(sql, rows)


def _raw_bulk_update(model, objs, field_names):
    """
    纯UPDATE（不是upsert）——批量更新"已经确定存在"的行，不需要、也不会
    新建行。2026-09-30新增，给compute_seo_tier用，原因是踩到了
    _raw_bulk_upsert的一个隐藏限制：

    `INSERT INTO t (少数几列) VALUES (...) ON DUPLICATE KEY UPDATE ...`
    这条SQL，MySQL在真正判断"这一行到底是插入还是更新"之前，会先按
    NOT NULL约束校验一遍"如果要插入，这一整行凑不凑得齐"——即使最终
    100%都会命中已有主键、走UPDATE分支，那个"候选插入行"依然要满足
    NOT NULL约束。compute_seo_tier只想更新seo_tier这4个字段，但
    Extension的name/slug等字段是NOT NULL且没有数据库级默认值（Django的
    default=""只是ORM层面的默认值，不是MySQL schema里的真DEFAULT——这个
    坑这次已经连续踩了两次），实测直接报"Field 'name' doesn't have a
    default value"，即使表里这些行早就存在。

    plain UPDATE不存在这个问题——它不需要构造一整行，只改WHERE条件命中的
    已有行的指定列，天然不受其它列是否NOT NULL/有没有默认值影响。

    适用前提：调用方必须确保这些行在表里已经存在（compute_seo_tier满足
    这一点，因为所有对象都是从Extension.objects.all()查出来的）——如果
    传进来的某个主键实际不存在，UPDATE只是静默影响0行，不会报错也不会
    新建，这跟_raw_bulk_upsert"不存在就插入"的语义不同，用错场景需要
    调用方自己注意。

    field_names 第一个仍然是主键字段（用于WHERE子句），其余是要更新的列。
    """
    table = model._meta.db_table
    pk_field_name = field_names[0]
    update_field_names = field_names[1:]

    pk_column = model._meta.get_field(pk_field_name).column
    pk_attname = model._meta.get_field(pk_field_name).attname
    update_columns = [model._meta.get_field(f).column for f in update_field_names]
    update_attnames = [model._meta.get_field(f).attname for f in update_field_names]

    set_clause = ", ".join(f"`{c}`=%s" for c in update_columns)
    sql = f"UPDATE `{table}` SET {set_clause} WHERE `{pk_column}`=%s"

    rows = [
        tuple(_sanitize_for_mysql(getattr(obj, a)) for a in update_attnames) + (getattr(obj, pk_attname),)
        for obj in objs
    ]
    with connection.cursor() as cursor:
        cursor.executemany(sql, rows)


def sync_rows(df_subset, stdout, with_chart: bool = True) -> int:
    """
    给定 metrics.compute_metrics() 结果的一个子集(DataFrame)，把每一行写入
    Extension / ExtensionMetric。

    with_chart=True 时，额外现场调用一次 plugin_history 算历史曲线，写入
    ExtensionChart——这一步每个插件约1分钟，只有真正需要展示这个插件的
    历史曲线（Extension Detail 页面）时才需要。Home/Dataset 这类只展示
    "当前状态"的页面，不应该带上这一步，否则就是白白等待，数据也用不上
    （Dataset 卖的是完整数据包，不是挑几个插件展示历史，历史曲线只属于
    单个插件的 Detail 页面）。

    返回实际同步的行数。
    """
    plugin_history = get_plugin_history() if with_chart else None
    count = 0

    for _, row in df_subset.iterrows():
        extension, _ = Extension.objects.update_or_create(
            extension_id=row["id"],
            defaults=dict(
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
            ),
        )
        ExtensionMetric.objects.update_or_create(
            extension=extension,
            defaults=dict(
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
            ),
        )

        if with_chart:
            stdout.write(f"  正在扫描 {row['id']} 的历史曲线（现场读全部parquet，约1分钟）...")
            history_df = plugin_history.fetch_plugin_history(row["id"])
            milestones_df = plugin_history.compute_first_milestones(history_df)
            ExtensionChart.objects.update_or_create(
                extension=extension,
                defaults=dict(
                    history=json.loads(history_df.to_json(orient="records")),
                    milestones=json.loads(milestones_df.to_json(orient="records")),
                ),
            )

        stdout.write(f"已同步: {row['id']} ({row['name']})")
        count += 1

    return count
