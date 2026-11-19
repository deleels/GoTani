from django.urls import path

from . import views

app_name = "farm"

urlpatterns = [
    path("", views.CyclesView.as_view(), name="cycles"),
    path("elements/new/", views.ElementCreateView.as_view(), name="element_create"),
    path("elements/<int:pk>/", views.ElementDetailView.as_view(), name="element_detail"),
    path("cycles/<int:pk>/", views.CycleDetailView.as_view(), name="cycle_detail"),
    path("inputs/<int:pk>/", views.InputDetailView.as_view(), name="input_detail"),
    path("preharvest/new/", views.PreHarvestCheckView.as_view(), name="preharvest_new"),
    path("elements/<int:pk>/toggle/", views.ElementToggleView.as_view(), name="element_toggle"),
    path("cycles/<int:pk>/stage/", views.AdvanceStageView.as_view(), name="cycle_stage"),
    path("activities/", views.ActivitiesView.as_view(), name="activities"),
    path("activities/<int:pk>/delete/", views.ActivityDeleteView.as_view(), name="activity_delete"),
    path("inputs/", views.InputsView.as_view(), name="inputs"),
    path("expenses/", views.ExpensesView.as_view(), name="expenses"),
    path("expenses/<int:pk>/approve/", views.ExpenseActionView.as_view(action="approve"), name="expense_approve"),
    path("expenses/<int:pk>/reject/", views.ExpenseActionView.as_view(action="reject"), name="expense_reject"),
    path("expenses/<int:pk>/revert/", views.ExpenseActionView.as_view(action="revert"), name="expense_revert"),
    path("expenses/<int:pk>/delete/", views.ExpenseActionView.as_view(action="delete"), name="expense_delete"),
    path("harvests/", views.HarvestsView.as_view(), name="harvests"),
    path("harvests/<int:pk>/delete/", views.HarvestDeleteView.as_view(), name="harvest_delete"),
    path("spoilage/", views.SpoilageView.as_view(), name="spoilage"),
    path("prices/", views.PricesView.as_view(), name="prices"),
]
