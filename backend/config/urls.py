"""프로젝트 루트 URL."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("onboarding.urls")),
    path("api/v1/", include("agents.urls")),
]
