"""
自定义 allauth adapter。

问题：我们自己的邮箱+密码注册（views.py 的 register()）用的是普通
User.objects.create_user()，不会往 allauth 的 EmailAddress 表里写记录。
allauth 默认"按已验证邮箱自动关联到已有账号"的逻辑依赖那张表，找不到匹配，
所以如果一个人先用邮箱+密码注册过，后来又点"使用Google继续"、用的是同一个
邮箱，默认行为会新建一个完全独立的账号——同一个邮箱变成两个互不相通的账号，
一个只能密码登录、一个只能Google登录，对用户来说是很困惑的一个bug。

解决：Google登录时，如果发现已经有一个 Django User 用同一个邮箱注册过，
直接关联到那个已有账号，不新建。
"""
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth.models import User


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        if sociallogin.is_existing:
            return

        email = sociallogin.account.extra_data.get("email")
        if not email:
            return

        try:
            existing_user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return

        sociallogin.connect(request, existing_user)
