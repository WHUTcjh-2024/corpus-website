from __future__ import annotations

from django.contrib.auth import login
from django.conf import settings
from django.middleware.csrf import get_token
from django.db.models import Prefetch, Sum
from django.shortcuts import resolve_url
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import generics, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.forms import ApprovedUserAuthenticationForm
from apps.accounts.permissions import get_user_profile, workspace_access_scope
from apps.corpora.models import Corpus, CorpusStatus
from apps.corpora.services import (
    can_create_personal_corpus,
    can_upload_personal_corpus,
    upload_limits_for,
    uploaded_bytes_for,
    visible_corpora_for,
)
from apps.exports.models import ExportJob
from apps.processing.models import ProcessingTask

from .permissions import HasWorkspaceAccess
from .serializers import (
    CorpusDetailSerializer,
    CorpusSerializer,
    ExportJobSerializer,
    UserProfileSerializer,
    UserSerializer,
)


def with_latest_tasks(queryset):
    return queryset.prefetch_related(
        Prefetch(
            "processing_tasks",
            queryset=ProcessingTask.objects.order_by("-created_at"),
            to_attr="task_history",
        )
    )


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def health(request):
    return Response({"status": "ok", "stage": settings.PLATFORM_STAGE})


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"csrf_token": get_token(request)})


class SessionView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        user = request.user
        profile = get_user_profile(user)
        access_scope = workspace_access_scope(user)
        return Response(
            {
                "is_authenticated": user.is_authenticated,
                "access_scope": str(access_scope),
                "user": UserSerializer(user).data if user.is_authenticated else None,
                "profile": UserProfileSerializer(profile).data if profile else None,
            }
        )


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        form = ApprovedUserAuthenticationForm(request=request, data=request.data)
        if not form.is_valid():
            return Response(
                {
                    "detail": "用户名、密码错误，或账号尚未审核通过。",
                    "errors": form.errors,
                },
                status=400,
            )

        login(request, form.get_user())
        return Response({"redirect_to": resolve_url(settings.LOGIN_REDIRECT_URL)})


class DashboardView(APIView):
    permission_classes = [HasWorkspaceAccess]

    def get(self, request):
        visible_corpora = visible_corpora_for(request.user).select_related("documentation")
        export_jobs = ExportJob.objects.filter(requested_by=request.user).select_related("corpus")
        limits = upload_limits_for(request.user)
        recent_corpora = with_latest_tasks(visible_corpora.order_by("-updated_at")[:5])
        return Response(
            {
                "metrics": {
                    "corpus_count": visible_corpora.count(),
                    "ready_corpus_count": visible_corpora.filter(status=CorpusStatus.READY).count(),
                    "token_count": visible_corpora.aggregate(total=Sum("documentation__token_count"))[
                        "total"
                    ]
                    or 0,
                    "export_count": export_jobs.count(),
                    "uploaded_bytes": uploaded_bytes_for(request.user),
                    "upload_total_bytes": limits.total_bytes,
                    "upload_max_file_bytes": limits.max_file_bytes,
                },
                "recent_corpora": CorpusSerializer(recent_corpora, many=True).data,
                "recent_exports": ExportJobSerializer(export_jobs[:5], many=True).data,
                "capabilities": {
                    "can_create_personal": can_create_personal_corpus(request.user),
                    "can_upload_personal": can_upload_personal_corpus(request.user),
                },
            }
        )


class CorpusListView(generics.ListAPIView):
    serializer_class = CorpusSerializer
    permission_classes = [HasWorkspaceAccess]

    def get_queryset(self):
        queryset = visible_corpora_for(self.request.user).select_related("owner", "documentation")
        queryset = with_latest_tasks(queryset)

        filters = {
            "source_type": "source_type",
            "status": "status",
            "language": "language",
            "corpus_type": "corpus_type",
        }
        for param, field in filters.items():
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})

        query = self.request.query_params.get("q")
        if query:
            queryset = queryset.filter(name__icontains=query.strip())

        return queryset.order_by("name", "-updated_at")

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response.data = {
            "results": response.data,
            "capabilities": {
                "can_create_personal": can_create_personal_corpus(request.user),
                "can_upload_personal": can_upload_personal_corpus(request.user),
            },
        }
        return response


class CorpusDetailView(generics.RetrieveAPIView):
    serializer_class = CorpusDetailSerializer
    permission_classes = [HasWorkspaceAccess]

    def get_queryset(self):
        return with_latest_tasks(
            visible_corpora_for(self.request.user)
            .select_related("owner", "documentation")
            .prefetch_related("files")
        )
