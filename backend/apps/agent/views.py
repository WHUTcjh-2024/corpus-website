from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import HasWorkspaceAccess
from apps.corpora.services import visible_corpora_for

from .models import AgentRun
from .serializers import AgentRunCreateSerializer, AgentRunSerializer
from .services import (
    AgentRunNotReady,
    approve_agent_action,
    cancel_agent_run,
    create_agent_run,
    dispatch_agent_run,
)


class AgentRunListCreateView(APIView):
    permission_classes = [HasWorkspaceAccess]

    def get(self, request):
        runs = (
            AgentRun.objects.filter(
                requested_by=request.user,
                corpus__in=visible_corpora_for(request.user),
            )
            .select_related("corpus")
            .prefetch_related("steps", "approval")[:100]
        )
        return Response({"results": AgentRunSerializer(runs, many=True).data})

    def post(self, request):
        serializer = AgentRunCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        idempotency_key = request.headers.get("Idempotency-Key", "")
        if not idempotency_key.strip():
            return Response(
                {"detail": "Idempotency-Key header is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = serializer.validated_data
        try:
            run, created = create_agent_run(
                user=request.user,
                corpus=data["corpus_id"],
                mode=data["mode"],
                query=data.get("query", ""),
                language=data.get("language"),
                max_results=data["max_results"],
                idempotency_key=idempotency_key,
                request_id=request.headers.get("X-Request-Id"),
                request=request,
            )
            if created:
                dispatch_agent_run(run)
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except (ValidationError, AgentRunNotReady) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        payload = AgentRunSerializer(
            AgentRun.objects.select_related("corpus").prefetch_related("steps", "approval").get(pk=run.pk)
        ).data
        response = Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        response["X-Request-Id"] = run.request_id
        return response


class AgentRunDetailView(APIView):
    permission_classes = [HasWorkspaceAccess]

    def get(self, request, pk):
        run = get_object_or_404(
            AgentRun.objects.select_related("corpus").prefetch_related("steps", "approval"),
            pk=pk,
            requested_by=request.user,
            corpus__in=visible_corpora_for(request.user),
        )
        response = Response(AgentRunSerializer(run).data)
        response["X-Request-Id"] = run.request_id
        return response


class AgentRunApprovalView(APIView):
    permission_classes = [HasWorkspaceAccess]

    def post(self, request, pk):
        try:
            approve_agent_action(run_id=pk, user=request.user, request=request)
        except AgentRun.DoesNotExist:
            return Response({"detail": "Agent run does not exist."}, status=status.HTTP_404_NOT_FOUND)
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        run = get_object_or_404(
            AgentRun.objects.select_related("corpus").prefetch_related("steps", "approval"),
            pk=pk,
            requested_by=request.user,
            corpus__in=visible_corpora_for(request.user),
        )
        response = Response(AgentRunSerializer(run).data)
        response["X-Request-Id"] = run.request_id
        return response


class AgentRunCancelView(APIView):
    permission_classes = [HasWorkspaceAccess]

    def post(self, request, pk):
        try:
            run = cancel_agent_run(run_id=pk, user=request.user, request=request)
        except AgentRun.DoesNotExist:
            return Response({"detail": "Agent run does not exist."}, status=status.HTTP_404_NOT_FOUND)
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        run = get_object_or_404(
            AgentRun.objects.select_related("corpus").prefetch_related("steps", "approval"),
            pk=run.pk,
            requested_by=request.user,
            corpus__in=visible_corpora_for(request.user),
        )
        response = Response(AgentRunSerializer(run).data)
        response["X-Request-Id"] = run.request_id
        return response
