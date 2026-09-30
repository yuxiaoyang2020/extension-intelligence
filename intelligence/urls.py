from django.contrib.auth import views as auth_views
from django.urls import path

from . import billing_views, views
from .forms import EmailAuthenticationForm

app_name = "intelligence"

urlpatterns = [
    path("", views.home, name="home"),
    path("dataset/", views.dataset, name="dataset"),
    path("dataset/sample.csv", views.dataset_sample_csv, name="dataset_sample_csv"),
    # 正式历史文件下载——校验套餐权限后跳转到OSS的签名链接，不代理下载。
    path("dataset/download/<str:date_str>/", views.dataset_download, name="dataset_download"),
    path("rankings/", views.rankings, name="rankings"),
    path("explorer/", views.explorer, name="explorer"),
    path("pricing/", views.pricing, name="pricing"),
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
    path("methodology/", views.methodology, name="methodology"),
    path("data-dictionary/", views.data_dictionary, name="data_dictionary"),
    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),
    # 首发内容页（见ROADMAP"首发新增"）：Category/Best Pages这次只做
    # Productivity这一个类目验证效果，URL用干净的静态路径（不是
    # ?category=xxx查询参数），本身就是可独立索引的落地页。
    path("category/productivity/", views.category_productivity, name="category_productivity"),
    path("best-productivity-chrome-extensions/", views.best_productivity, name="best_productivity"),
    # Research/Insights——首发先搭结构+1篇真实数据研究内容，不批量生成AI Blog。
    path("research/", views.research_index, name="research_index"),
    path("research/<slug:slug>/", views.research_detail, name="research_detail"),
    # Stripe：Pricing页点"升级"发起付款、Account页点"管理订阅"进Billing
    # Portal、webhook接收Stripe的订阅状态变化回调（真正更新plan字段的地方）。
    path("billing/checkout/<str:plan>/", billing_views.create_checkout, name="billing_checkout"),
    path("billing/portal/", billing_views.create_portal, name="billing_portal"),
    path("billing/webhook/", billing_views.stripe_webhook, name="stripe_webhook"),
    # 账号：先做邮箱+密码，用Django自带auth视图（登录/登出）+ 我们自己写的注册视图。
    # 以后加谷歌登录时，django-allauth 会加在这套现成的User模型上，这几条路由不用改。
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="intelligence/login.html", authentication_form=EmailAuthenticationForm),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(next_page="intelligence:home"), name="logout"),
    path("accounts/register/", views.register, name="register"),
    path("account/", views.account, name="account"),
    # 自己写的轻量内部管理页，只有 is_staff 账号能访问。
    path("staff/", views.staff_dashboard, name="staff_dashboard"),
    path("staff/users/<int:user_id>/plan/", views.staff_update_plan, name="staff_update_plan"),
    # SEO友好的独立URL：/extension/{真实插件id}/{可读slug}/
    # slug 目前只用于展示/URL可读性，不参与查询（查询只认 extension_id）。
    path("extension/<str:extension_id>/<slug:slug>/", views.extension_detail, name="extension_detail"),
]
