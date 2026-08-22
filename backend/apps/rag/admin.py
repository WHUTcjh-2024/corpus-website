from django.contrib import admin

from .models import RagIndex


@admin.register(RagIndex)
class RagIndexAdmin(admin.ModelAdmin):
    list_display = (
        "corpus",
        "status",
        "embedding_model",
        "vector_dimension",
        "chunk_count",
        "attempt_count",
        "updated_at",
    )
    list_filter = ("status", "embedding_model")
    search_fields = ("corpus__name", "corpus__id", "chunk_manifest_sha256")
    autocomplete_fields = ("corpus", "processing_task")
    readonly_fields = (
        "id",
        "corpus",
        "processing_task",
        "status",
        "chunk_manifest_sha256",
        "embedding_model",
        "vector_dimension",
        "chunk_count",
        "vector_count",
        "artifact_path",
        "error_message",
        "attempt_count",
        "locked_until",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
