from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User

from .models import ResearchRequest


class EmailAuthenticationForm(AuthenticationForm):
    """
    Django自带的登录表单字段叫 username，我们只是把界面标签换成"邮箱"
    （因为正常注册用户的用户名就是邮箱）。

    【注意】这里故意用 CharField 而不是 EmailField——之前用 EmailField
    强制校验邮箱格式，导致 python manage.py createsuperuser 建的账号
    （用户名可以是任意字符串，比如 root，不一定是邮箱格式）反而登录不了。
    CharField 不做格式限制，普通用户和管理员账号都能正常登录。
    """

    username = forms.CharField(label="邮箱")


class RegisterForm(forms.Form):
    """极简注册：邮箱+密码，不做复杂 onboarding。邮箱同时当 Django User 的
    username 用（Django自带User模型要求一个username字段，这样处理不用换
    成自定义User模型）。"""

    email = forms.EmailField(label="邮箱")
    password = forms.CharField(label="密码", widget=forms.PasswordInput, min_length=8)

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(username=email).exists():
            raise forms.ValidationError("这个邮箱已经注册过了")
        return email


class ResearchRequestForm(forms.ModelForm):
    class Meta:
        model = ResearchRequest
        fields = ["title", "description", "output_format", "attachment"]
        labels = {
            "title": "需求标题",
            "description": "需求描述",
            "output_format": "希望输出格式",
            "attachment": "可选附件",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}