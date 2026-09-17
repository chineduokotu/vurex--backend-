import logging
import signal
import threading

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import close_old_connections, connection

from integrations.termii import TermiiError, validate_configuration
from notifications.worker import process_one, recover_expired_leases

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the live Termii dispatch and delivery-receipt worker."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("The notification worker requires PostgreSQL.")
        try:
            validate_configuration(require_webhook=settings.TERMII_WEBHOOKS_ENABLED)
        except TermiiError as exc:
            raise CommandError("Termii configuration is incomplete: " + exc.code) from None
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        self.stdout.write("Notification worker started. Real SMS dispatch is enabled by configuration.")
        while not stop.is_set():
            close_old_connections()
            try:
                recover_expired_leases()
                worked = process_one()
            except Exception:
                logger.error("notification_worker_iteration_failed")
                worked = False
            if not worked:
                stop.wait(2)
        self.stdout.write("Notification worker stopped.")
