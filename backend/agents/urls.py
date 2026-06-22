"""에이전트 API 라우팅."""
from django.urls import path

from .views import AdviseView

urlpatterns = [
    path("agents/advise/", AdviseView.as_view(), name="agents-advise"),
]
