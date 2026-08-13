import importlib


Migration = importlib.import_module(
    "apps.processing.migrations.0002_processingtask_one_active_per_user"
).Migration
