from django.contrib.auth.decorators import login_required
from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("staf/", views.StaffView.as_view(), name="staff"),
    path("staf/<int:pk>/<str:action>/", views.StaffActionView.as_view(), name="staff_action"),
    path("akun/profil/", views.ProfileView.as_view(), name="profile"),
    path("notifikasi/", views.NotificationsView.as_view(), name="notifications"),
    path("pengaturan/", views.SettingsView.as_view(), name="settings"),
    path("akun/kata-sandi/", login_required(views.PasswordChangeView.as_view()), name="password_change"),
]
