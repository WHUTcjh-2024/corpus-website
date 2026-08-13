from django.db import migrations, models


def migrate_remote_mode_to_queue(apps, schema_editor):
    ParallelAudit = apps.get_model("audits", "ParallelAudit")
    ParallelAudit.objects.filter(execution_mode="remote").update(execution_mode="queue")


class Migration(migrations.Migration):
    dependencies = [("audits", "0002_remote_auditor_service")]

    operations = [
        migrations.RenameField(
            model_name="parallelaudit",
            old_name="remote_job_id",
            new_name="worker_job_id",
        ),
        migrations.RenameField(
            model_name="parallelaudit",
            old_name="remote_state",
            new_name="worker_state",
        ),
        migrations.RenameField(
            model_name="parallelaudit",
            old_name="remote_attempt",
            new_name="worker_attempt",
        ),
        migrations.RenameField(
            model_name="parallelaudit",
            old_name="remote_callback_received_at",
            new_name="result_received_at",
        ),
        migrations.RenameField(
            model_name="parallelaudit",
            old_name="remote_callback_payload_hash",
            new_name="result_payload_hash",
        ),
        migrations.AddField(
            model_name="parallelaudit",
            name="command_message_id",
            field=models.CharField(blank=True, max_length=64, verbose_name="Command stream message ID"),
        ),
        migrations.AddField(
            model_name="parallelaudit",
            name="command_published_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Command published at"),
        ),
        migrations.AddField(
            model_name="parallelaudit",
            name="result_message_id",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True, verbose_name="Result stream message ID"),
        ),
        migrations.RunPython(migrate_remote_mode_to_queue, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="parallelaudit",
            name="execution_mode",
            field=models.CharField(
                choices=[("queue", "Redis Streams + Go worker"), ("local", "Local fallback")],
                default="queue",
                max_length=20,
                verbose_name="Execution mode",
            ),
        ),
    ]
