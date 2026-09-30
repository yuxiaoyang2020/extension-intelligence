from django.contrib import admin

from .models import Extension, ExtensionChart, ExtensionMetric, ResearchRequest, UserProfile


@admin.register(Extension)
class ExtensionAdmin(admin.ModelAdmin):
    list_display = ("extension_id", "name", "category", "developer", "synced_at")
    search_fields = ("extension_id", "name")


@admin.register(ExtensionMetric)
class ExtensionMetricAdmin(admin.ModelAdmin):
    list_display = ("extension", "user_count", "user_count_change_7d", "user_count_change_30d", "computed_at")


@admin.register(ExtensionChart)
class ExtensionChartAdmin(admin.ModelAdmin):
    list_display = ("extension", "computed_at")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    # 真实Stripe接入前，在这里手动改 plan 字段来模拟不同方案效果。
    list_display = ("user", "plan", "created_at")
    list_editable = ("plan",)
    search_fields = ("user__username", "user__email")


@admin.register(ResearchRequest)
class ResearchRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "output_format", "created_at")
    list_filter = ("output_format",)
