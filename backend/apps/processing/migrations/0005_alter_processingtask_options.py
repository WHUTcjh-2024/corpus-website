from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("processing", "0004_processingtask_created_sequence")]

    operations = [
        migrations.AlterModelOptions(
            name="processingtask",
            options={
                "ordering": ["-created_at", "-created_sequence"],
                "verbose_name": "加工任务",
                "verbose_name_plural": "加工任务",
            },
        ),
    ]
