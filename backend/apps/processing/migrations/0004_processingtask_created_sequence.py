from django.db import migrations, models
from django.db.models.expressions import RawSQL


class Migration(migrations.Migration):
    dependencies = [("processing", "0003_query_indexes")]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE SEQUENCE processing_task_created_sequence_seq AS bigint "
                "START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1"
            ),
            reverse_sql="DROP SEQUENCE processing_task_created_sequence_seq",
        ),
        migrations.AddField(
            model_name="processingtask",
            name="created_sequence",
            field=models.BigIntegerField(
                db_default=RawSQL("nextval('processing_task_created_sequence_seq')", []),
                editable=False,
                unique=True,
            ),
        ),
        migrations.RemoveIndex(
            model_name="processingtask",
            name="processing_corpus_created_idx",
        ),
        migrations.AddIndex(
            model_name="processingtask",
            index=models.Index(
                fields=["corpus", "-created_at", "-created_sequence"],
                name="proc_corpus_created_seq_idx",
            ),
        ),
    ]
