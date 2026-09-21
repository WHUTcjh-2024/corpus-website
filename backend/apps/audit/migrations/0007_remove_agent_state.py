from django.db import migrations


AGENT_TASK_NAMES = (
    "agent.run_corpus_agent",
    "agent.resume_corpus_agent",
)
AGENT_TABLES = (
    "agent_agentapproval",
    "agent_agentstep",
    "agent_agentrun",
)


def remove_agent_state(apps, schema_editor) -> None:
    """Purge persisted state left by the removed Agent application."""
    audit_event = apps.get_model("audit", "AuditEvent")
    outbox_event = apps.get_model("outbox", "OutboxEvent")
    content_type = apps.get_model("contenttypes", "ContentType")

    audit_event.objects.filter(event_type__startswith="agent.").delete()
    outbox_event.objects.filter(task_name__in=AGENT_TASK_NAMES).delete()
    content_type.objects.filter(app_label="agent").delete()

    connection = schema_editor.connection
    existing_tables = set(connection.introspection.table_names())
    for table_name in AGENT_TABLES:
        if table_name in existing_tables:
            schema_editor.execute(
                f"DROP TABLE IF EXISTS {connection.ops.quote_name(table_name)}"
            )


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0006_alter_auditevent_event_type"),
        ("outbox", "0008_alter_outboxevent_task_name"),
    ]

    operations = [
        migrations.RunPython(remove_agent_state, migrations.RunPython.noop),
    ]
