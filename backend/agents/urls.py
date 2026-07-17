"""에이전트 API 라우팅."""
from django.urls import path

from .views import (
    ActionDetailView, ActionsView, AdviseView, ChatView, HistoryView,
    ReportDetailView, ReportView,
)

urlpatterns = [
    path("agents/advise/", AdviseView.as_view(), name="agents-advise"),
    path("agents/chat/", ChatView.as_view(), name="agents-chat"),
    path("agents/history/<int:merchant_id>/", HistoryView.as_view(), name="agents-history"),
    path("agents/actions/", ActionsView.as_view(), name="agents-actions"),
    path("agents/actions/<int:action_id>/", ActionDetailView.as_view(), name="agents-action"),
    path("reports/", ReportView.as_view(), name="reports"),
    path("reports/<int:report_id>/", ReportDetailView.as_view(), name="report-detail"),
]
