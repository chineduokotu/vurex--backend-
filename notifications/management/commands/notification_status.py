import json

from django.core.management.base import BaseCommand

from notifications.services import status_summary


class Command(BaseCommand):
    help = "Show aggregate dispatch/delivery counts without phone numbers or message content."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(status_summary(), indent=2))
