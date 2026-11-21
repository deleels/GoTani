from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User

from .models import AuditLog, FarmProfile, UserProfile


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: the log is append-only (D82)."""

    list_display = ("created_at", "actor", "action", "summary")
    list_filter = ("action",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FarmProfile)
class FarmProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "latitude", "longitude", "updated_at")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "must_change_password", "notifications_seen_at")


class _ViewOnly:
    """Staff and roles change only on Staf, which enforces the last-OWNER rule (D123)."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ViewOnlyUserAdmin(_ViewOnly, UserAdmin):
    pass


class ViewOnlyGroupAdmin(_ViewOnly, GroupAdmin):
    pass


for _model, _admin in ((User, ViewOnlyUserAdmin), (Group, ViewOnlyGroupAdmin)):
    if admin.site.is_registered(_model):
        admin.site.unregister(_model)
    admin.site.register(_model, _admin)
