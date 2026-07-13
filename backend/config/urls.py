"""프로젝트 루트 URL."""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(request):
    """Render 헬스체크용 — 무인증 200. provider로 라이브/데모 여부 즉시 확인(키값은 미노출)."""
    from agents.llm import get_provider

    return JsonResponse({"status": "ok", "service": "geo-marketing-api",
                         "provider": get_provider().name})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", health),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("onboarding.urls")),
    path("api/v1/", include("agents.urls")),
]
