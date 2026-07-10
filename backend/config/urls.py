"""프로젝트 루트 URL."""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(request):
    """Render 헬스체크용 — 무인증 200."""
    return JsonResponse({"status": "ok", "service": "geo-marketing-api"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", health),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("onboarding.urls")),
    path("api/v1/", include("agents.urls")),
]
