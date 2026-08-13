from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("processing", "0003_query_indexes")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.AddField(
                    model_name="processingtask",
                    name="created_sequence",
                    field=models.BigIntegerField(default=0, editable=False, unique=True),
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="processingtask",
                    name="created_sequence",
                    field=models.BigIntegerField(default=0, editable=False, unique=True),
                ),
            ],
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
