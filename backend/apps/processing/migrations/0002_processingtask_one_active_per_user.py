from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("processing", "0001_initial")]

    operations = [
        migrations.AddConstraint(
            model_name="processingtask",
            constraint=models.UniqueConstraint(
                condition=(
                    models.Q(("requested_by__isnull", False))
                    & models.Q(("status__in", ["pending", "running"]))
                ),
                fields=("requested_by",),
                name="one_active_processing_task_per_user",
            ),
        )
    ]
