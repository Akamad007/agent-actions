from django.urls import include, path

urlpatterns = [
    path("agent-actions/", include("django_agent_actions.urls")),
]
