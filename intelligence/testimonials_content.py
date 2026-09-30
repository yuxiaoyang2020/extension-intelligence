"""
Testimonials区域的内容来源——2026-09-30新增。

明确要求："可以保留UI结构，但没有真实ExtensionCenter Intelligence客户评价
时，生产环境不要展示虚假评价"。现在一个真实客户评价都没有，所以这个列表
刻意留空——首页对应区块会因为列表为空而完全不渲染（见home.html里
{% if testimonials %}的判断），不会有任何编造的用户评价出现在生产环境。

以后真的收集到真实客户愿意公开署名的评价，往这个列表里加字典就行，格式：
{"quote": "...", "author_name": "...", "author_title": "...", "company": "..."}
不需要动模板/视图代码。
"""
from __future__ import annotations

TESTIMONIALS = []


def get_testimonials():
    return TESTIMONIALS
