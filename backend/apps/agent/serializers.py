from __future__ import annotations

from rest_framework import serializers

from apps.corpora.models import Corpus

from .models import AgentApproval, AgentRun, AgentStep


class AgentRunCreateSerializer(serializers.Serializer):
    corpus_id = serializers.UUIDField()
    mode = serializers.ChoiceField(choices=("retrieve", "quality_review", "export"))
    query = serializers.CharField(required=False, allow_blank=True, max_length=200)
    language = serializers.ChoiceField(choices=("zh", "en"), required=False, allow_null=True)
    max_results = serializers.IntegerField(required=False, default=5, min_value=1, max_value=10)

    def validate_corpus_id(self, value):
        try:
            return Corpus.objects.get(pk=value)
        except Corpus.DoesNotExist as exc:
            raise serializers.ValidationError("Corpus does not exist.") from exc

    def validate(self, attrs):
        if attrs["mode"] in {"retrieve", "export"} and not attrs.get("query", "").strip():
            raise serializers.ValidationError({"query": "This mode requires a query."})
        return attrs


class AgentStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentStep
        fields = (
            "sequence", "node", "tool_name", "status", "input", "output", "error_code",
            "error_message", "attempt_count", "started_at", "finished_at",
        )


class AgentApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentApproval
        fields = ("id", "action", "payload", "status", "expires_at", "result", "resolved_at")


class AgentRunSerializer(serializers.ModelSerializer):
    steps = AgentStepSerializer(many=True, read_only=True)
    approval = AgentApprovalSerializer(read_only=True)
    corpus_id = serializers.UUIDField(source="corpus.pk", read_only=True)

    class Meta:
        model = AgentRun
        fields = (
            "id", "corpus_id", "mode", "skill", "request_id", "status", "answer", "evidence",
            "model_usage", "estimated_cost_usd", "error_code", "error_message", "attempt_count",
            "external_wait_kind", "external_wait_id", "external_wait_expires_at",
            "started_at", "finished_at", "created_at", "updated_at", "steps", "approval",
        )
