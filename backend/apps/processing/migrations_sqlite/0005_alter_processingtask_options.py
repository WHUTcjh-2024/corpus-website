import importlib


Migration = importlib.import_module(
    "apps.processing.migrations.0005_alter_processingtask_options"
).Migration
