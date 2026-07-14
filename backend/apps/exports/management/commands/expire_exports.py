from django.core.management.base import BaseCommand

from apps.exports.services import expire_exports


class Command(BaseCommand):
    help = "Mark expired export jobs and remove their generated files."

    def handle(self, *args, **options) -> None:
        count = expire_exports()
        self.stdout.write(self.style.SUCCESS(f"Expired {count} export job(s)."))
