from django.contrib import admin

from .models import FarmProfile, UserProfile


@admin.register(FarmProfile)
class FarmProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "latitude", "longitude", "updated_at")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "must_change_password", "notifications_seen_at")
