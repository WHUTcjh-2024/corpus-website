from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("corpora", "0006_corpus_access_grants"),
    ]

    operations = [
        migrations.AddField(
            model_name="corpusfile",
            name="pair_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="批量双语语料中用于关联一组中英文源文件。",
                max_length=64,
                verbose_name="双语配对 ID",
            ),
        ),
    ]
