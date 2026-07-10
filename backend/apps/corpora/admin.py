from django.contrib import admin

from .models import Corpus, CorpusDocumentation, CorpusFile


class CorpusDocumentationInline(admin.StackedInline):
    model = CorpusDocumentation
    extra = 0


class CorpusFileInline(admin.TabularInline):
    model = CorpusFile
    extra = 0
    fields = (
        "original_filename",
        "stored_path",
        "detected_type",
        "language",
        "encoding",
        "size_bytes",
        "status",
    )
    readonly_fields = ("size_bytes",)


@admin.register(Corpus)
class CorpusAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "source_type",
        "corpus_type",
        "language",
        "owner",
        "access_level",
        "status",
        "stage",
    )
    list_filter = ("source_type", "corpus_type", "language", "access_level", "status")
    search_fields = ("name", "description", "manifest_file_id", "manifest_relative_path")
    autocomplete_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at")
    inlines = (CorpusFileInline, CorpusDocumentationInline)


@admin.register(CorpusDocumentation)
class CorpusDocumentationAdmin(admin.ModelAdmin):
    list_display = ("corpus", "file_count", "document_count", "token_count", "updated_at")
    search_fields = ("corpus__name", "processing_notes", "copyright_notice")
    autocomplete_fields = ("corpus",)


@admin.register(CorpusFile)
class CorpusFileAdmin(admin.ModelAdmin):
    list_display = (
        "original_filename",
        "corpus",
        "detected_type",
        "language",
        "encoding",
        "size_bytes",
        "status",
    )
    list_filter = ("detected_type", "language", "status")
    search_fields = ("original_filename", "stored_path", "manifest_file_id", "corpus__name")
    autocomplete_fields = ("corpus",)
    readonly_fields = ("created_at", "updated_at")
