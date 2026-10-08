import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.core.models import EmailOutbox
from apps.core.notifications import retry_email_outbox_entry


class Command(BaseCommand):
    help = "Renvoie les courriels transactionnels chiffrés en attente."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Reste actif pour surveiller la file.")
        parser.add_argument("--interval", type=int, default=15, help="Pause en secondes entre les lots.")

    def handle(self, *args, **options):
        interval = max(2, min(300, options["interval"]))
        while True:
            count = self.process_due_messages()
            if not options["loop"]:
                self.stdout.write(self.style.SUCCESS(f"{count} courriel(s) repris."))
                return
            time.sleep(interval)

    def process_due_messages(self):
        now = timezone.now()
        EmailOutbox.objects.filter(status="sending", updated_at__lt=now - timedelta(minutes=10)).update(
            status="queued", next_attempt_at=now, last_error="Reprise après arrêt du processus",
        )
        entry_ids = list(EmailOutbox.objects.filter(
            status="queued", next_attempt_at__lte=now, encrypted_content__gt="",
        ).order_by("created_at").values_list("pk", flat=True)[:20])
        processed = 0
        for entry_id in entry_ids:
            with transaction.atomic():
                entry = EmailOutbox.objects.select_for_update().filter(
                    pk=entry_id, status="queued", next_attempt_at__lte=timezone.now(),
                ).first()
                if entry is None:
                    continue
                entry.status = "sending"
                entry.save(update_fields=["status", "updated_at"])
            retry_email_outbox_entry(entry)
            processed += 1
        return processed
