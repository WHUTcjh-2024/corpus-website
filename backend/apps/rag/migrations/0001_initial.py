from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("corpora", "0001_initial"),
        ("processing", "0005_alter_processingtask_options"),
    ]

    operations = [
        migrations.CreateModel(
            name="RagIndex",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("pending", "等待索引"), ("running", "索引构建中"), ("ready", "索引就绪"), ("failed", "索引失败")], db_index=True, default="pending", max_length=20)),
                ("chunk_manifest_sha256", models.CharField(blank=True, max_length=64)),
                ("embedding_model", models.CharField(blank=True, max_length=200)),
                ("vector_dimension", models.PositiveIntegerField(default=0)),
                ("chunk_count", models.PositiveIntegerField(default=0)),
                ("vector_count", models.PositiveIntegerField(default=0)),
                ("artifact_path", models.CharField(blank=True, max_length=1500)),
                ("error_message", models.TextField(blank=True)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("locked_until", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("corpus", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="rag_index", to="corpora.corpus", verbose_name="语料库")),
                ("processing_task", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="rag_indexes", to="processing.processingtask", verbose_name="来源加工任务")),
            ],
            options={"verbose_name": "RAG 索引", "verbose_name_plural": "RAG 索引"},
        ),
        migrations.AddIndex(
            model_name="ragindex",
            index=models.Index(fields=["status", "locked_until"], name="rag_index_lease_idx"),
        ),
        migrations.AddIndex(
            model_name="ragindex",
            index=models.Index(fields=["status", "-updated_at"], name="rag_index_status_updated_idx"),
        ),
    ]
