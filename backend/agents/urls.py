"""에이전트 API 라우팅."""
from django.urls import path

from .views import AdviseView, ChatView, HistoryView

urlpatterns = [
    path("agents/advise/", AdviseView.as_view(), name="agents-advise"),
    path("agents/chat/", ChatView.as_view(), name="agents-chat"),
    path("agents/history/<int:merchant_id>/", HistoryView.as_view(), name="agents-history"),
]
