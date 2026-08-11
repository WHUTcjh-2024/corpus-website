from __future__ import annotations

from django.contrib.auth.models import User
from rest_framework import serializers

from apps.accounts.models import UserProfile
from apps.corpora.models import Corpus, CorpusDocumentation, CorpusFile
from apps.exports.models import ExportJob
from apps.processing.models import ProcessingTask


class UserProfileSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = UserProfile
        fields = (
            "full_name",
            "organization",
            "email",
            "role",
            "role_label",
            "status",
            "status_label",
        )


class UserSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "email", "is_staff", "is_superuser", "display_name")

    def get_display_name(self, obj: User) -> str:
        return obj.get_full_name() or obj.get_username()


class ProcessingTaskSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ProcessingTask
        fields = (
            "id",
            "task_type",
            "status",
            "status_label",
            "progress",
            "error_message",
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
        )


class CorpusDocumentationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CorpusDocumentation
        fields = (
            "file_count",
            "document_count",
            "paragraph_count",
            "sentence_count",
            "token_count",
            "type_count",
            "segmentation_tool",
            "processing_notes",
            "copyright_notice",
            "corpus_created_at",
            "updated_at",
        )


class CorpusFileSerializer(serializers.ModelSerializer):
    detected_type_label = serializers.CharField(source="get_detected_type_display", read_only=True)
    language_label = serializers.CharField(source="get_language_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = CorpusFile
        fields = (
            "id",
            "original_filename",
            "detected_type",
            "detected_type_label",
            "language",
            "language_label",
            "size_bytes",
            "encoding",
            "status",
            "status_label",
            "error_message",
            "created_at",
            "updated_at",
        )


class CorpusSerializer(serializers.ModelSerializer):
    source_type_label = serializers.CharField(source="get_source_type_display", read_only=True)
    corpus_type_label = serializers.CharField(source="get_corpus_type_display", read_only=True)
    language_label = serializers.CharField(source="get_language_display", read_only=True)
    access_level_label = serializers.CharField(source="get_access_level_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    owner_username = serializers.CharField(source="owner.username", read_only=True)
    documentation = CorpusDocumentationSerializer(read_only=True)
    latest_task = serializers.SerializerMethodField()

    class Meta:
        model = Corpus
        fields = (
            "id",
            "name",
            "source_type",
            "source_type_label",
            "corpus_type",
            "corpus_type_label",
            "language",
            "language_label",
            "owner_username",
            "access_level",
            "access_level_label",
            "status",
            "status_label",
            "stage",
            "description",
            "documentation",
            "latest_task",
            "created_at",
            "updated_at",
        )

    def get_latest_task(self, obj: Corpus) -> dict | None:
        task_history = getattr(obj, "task_history", None)
        task = (
            task_history[0]
            if task_history
            else obj.processing_tasks.order_by("-created_at", "-created_sequence").first()
        )
        return ProcessingTaskSerializer(task).data if task else None


class CorpusDetailSerializer(CorpusSerializer):
    files = CorpusFileSerializer(many=True, read_only=True)

    class Meta(CorpusSerializer.Meta):
        fields = CorpusSerializer.Meta.fields + ("files",)


class ExportJobSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    corpus_name = serializers.CharField(source="corpus.name", read_only=True)

    class Meta:
        model = ExportJob
        fields = (
            "id",
            "corpus",
            "corpus_name",
            "kind",
            "kind_label",
            "status",
            "status_label",
            "progress",
            "row_count",
            "download_count",
            "error_message",
            "expires_at",
            "created_at",
            "updated_at",
        )
