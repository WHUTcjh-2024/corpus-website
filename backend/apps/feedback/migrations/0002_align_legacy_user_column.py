from __future__ import annotations

from django.db import migrations


TABLE_NAME = "feedback_feedbackticket"
LEGACY_COLUMN = "submitted_by_id"
CURRENT_COLUMN = "user_id"


def _column_names(schema_editor) -> set[str]:
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, TABLE_NAME)
    return {column.name for column in description}


def _rename_column(schema_editor, source: str, target: str) -> None:
    quote = schema_editor.quote_name
    schema_editor.execute(
        f"ALTER TABLE {quote(TABLE_NAME)} "
        f"RENAME COLUMN {quote(source)} TO {quote(target)}"
    )


def align_legacy_user_column(apps, schema_editor) -> None:
    columns = _column_names(schema_editor)
    if LEGACY_COLUMN in columns and CURRENT_COLUMN not in columns:
        _rename_column(schema_editor, LEGACY_COLUMN, CURRENT_COLUMN)


def restore_legacy_user_column(apps, schema_editor) -> None:
    columns = _column_names(schema_editor)
    if CURRENT_COLUMN in columns and LEGACY_COLUMN not in columns:
        _rename_column(schema_editor, CURRENT_COLUMN, LEGACY_COLUMN)


class Migration(migrations.Migration):
    dependencies = [
        ("feedback", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            align_legacy_user_column,
            reverse_code=restore_legacy_user_column,
        ),
    ]
