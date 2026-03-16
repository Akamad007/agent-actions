from django.urls import path

from django_agent_actions import views

urlpatterns = [
    path("health/", views.health, name="django_agent_actions_health"),
    path("actions/", views.list_actions, name="django_agent_actions_list"),
    path(
        "actions/<str:action_name>/execute/",
        views.ExecuteActionView.as_view(),
        name="django_agent_actions_execute",
    ),
    path("approvals/", views.list_approvals, name="django_agent_actions_approvals_list"),
    path(
        "approvals/<str:pk>/approve/",
        views.ApproveView.as_view(),
        name="django_agent_actions_approve",
    ),
    path(
        "approvals/<str:pk>/reject/",
        views.RejectView.as_view(),
        name="django_agent_actions_reject",
    ),
    path("audit-logs/", views.list_audit_logs, name="django_agent_actions_audit_logs"),
]
