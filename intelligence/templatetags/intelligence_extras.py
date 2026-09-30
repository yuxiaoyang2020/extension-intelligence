"""
自定义模板过滤器。

背景：metrics.py 里 user_growth_rate_1d/7d/30d 这几个字段是原始比率
（比如 0.15 代表增长15%），不是已经乘过100的百分比数字——这是 metrics.py
自己的设计（config.py/其它榜单代码里判断阈值时也是拿 0.1 当"10%"用，
两边保持一致）。但页面上要展示成"15.0%"这种人看的格式，需要在展示层
乘以100，不能直接 floatformat 之后接一个"%"（那样会把15%显示成0.2%，
差100倍）。
"""
from django import template

register = template.Library()


@register.filter
def pct(value, decimals=1):
    """把 metrics.py 的原始比率（0.15）格式化成百分比数字字符串（15.0），
    调用方自己在模板里接上 "%"。value 为 None 时返回空字符串。"""
    if value is None:
        return ""
    try:
        return f"{float(value) * 100:.{int(decimals)}f}"
    except (TypeError, ValueError):
        return ""


@register.filter
def fmt_users(value):
    """把用户数这类大整数格式化成 1.2K / 3.4M 这种人看的缩写——跟设计稿
    <script> 里的 fmtUsers() 是同一套规则（10K以下原样、10K-1M显示K、
    1M以上显示M），照抄过来，不是自己另外发明的格式。value为None返回"—"。"""
    if value is None:
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n < 10000:
        return f"{sign}{n:,.0f}"
    if n < 1_000_000:
        s = f"{n / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{sign}{s}K"
    s = f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".")
    return f"{sign}{s}M"


@register.filter
def fmt_delta(value):
    """跟 fmt_users 一样的缩写规则，但带 +/- 符号——给"增长了多少"这种
    有正负意义的数字用（对应设计稿的 fmtDelta()）。"""
    if value is None:
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if n >= 0 else "-"
    return f"{sign}{fmt_users(abs(n))}"
