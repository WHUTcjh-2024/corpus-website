from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("corpora", "0007_corpusfile_pair_id"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="corpus",
            index=models.Index(
                fields=["source_type", "status", "corpus_type", "name"],
                name="corpus_public_list_idx",
            ),
        ),
    ]
