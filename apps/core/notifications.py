import json
import logging
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import EmailOutbox

logger = logging.getLogger(__name__)
MAX_EMAIL_ATTEMPTS = 5


def _fernet():
    key = settings.EMAIL_OUTBOX_ENCRYPTION_KEY
    if not key:
        return None
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        logger.error("EMAIL_OUTBOX_ENCRYPTION_KEY est invalide ; les messages en échec ne seront pas mis en file.")
        return None


def send_transactional_email(*, subject, body, recipient):
    """Envoie un message et conserve un journal ; chiffre le contenu à reprendre si SMTP échoue."""
    try:
        sent = send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
        if not sent:
            raise RuntimeError("Backend e-mail sans destinataire accepté")
    except Exception as error:
        fernet = _fernet()
        encrypted = ""
        status = "failed"
        next_attempt_at = None
        if fernet:
            content = json.dumps({"subject": subject, "body": body, "recipient": recipient}, ensure_ascii=False).encode("utf-8")
            encrypted = fernet.encrypt(content).decode("ascii")
            status = "queued"
            next_attempt_at = timezone.now() + timedelta(minutes=1)
        entry = EmailOutbox.objects.create(
            recipient=recipient[:254], subject=subject[:240], encrypted_content=encrypted,
            status=status, attempts=1, last_error=type(error).__name__[:120], next_attempt_at=next_attempt_at,
        )
        logger.warning("Envoi du courriel impossible (entrée %s, %s).", entry.pk, type(error).__name__)
        return entry

    return EmailOutbox.objects.create(
        recipient=recipient[:254], subject=subject[:240], status="sent", attempts=1, sent_at=timezone.now(),
    )


def retry_email_outbox_entry(entry):
    """Tente de renvoyer une entrée réclamée par le processus de reprise."""
    fernet = _fernet()
    if not fernet or not entry.encrypted_content:
        entry.status = "failed"
        entry.last_error = "Clé de déchiffrement absente"
        entry.encrypted_content = ""
        entry.save(update_fields=["status", "last_error", "encrypted_content", "updated_at"])
        return False

    try:
        payload = json.loads(fernet.decrypt(entry.encrypted_content.encode("ascii")).decode("utf-8"))
        sent = send_mail(payload["subject"], payload["body"], settings.DEFAULT_FROM_EMAIL, [payload["recipient"]], fail_silently=False)
        if not sent:
            raise RuntimeError("Backend e-mail sans destinataire accepté")
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, KeyError, ValueError) as error:
        entry.status = "failed"
        entry.last_error = "Contenu chiffré illisible"
        entry.encrypted_content = ""
        entry.save(update_fields=["status", "last_error", "encrypted_content", "updated_at"])
        logger.error("Contenu du courriel impossible à déchiffrer (entrée %s).", entry.pk)
        return False
    except Exception as error:
        entry.attempts += 1
        entry.last_error = type(error).__name__[:120]
        entry.status = "failed" if entry.attempts >= MAX_EMAIL_ATTEMPTS else "queued"
        if entry.status == "queued":
            entry.next_attempt_at = timezone.now() + timedelta(minutes=min(60, 2 ** (entry.attempts - 1)))
        else:
            entry.encrypted_content = ""
        entry.save(update_fields=["attempts", "last_error", "status", "next_attempt_at", "encrypted_content", "updated_at"])
        logger.warning("Nouvel essai e-mail échoué (entrée %s, %s).", entry.pk, type(error).__name__)
        return False

    entry.attempts += 1
    entry.status = "sent"
    entry.last_error = ""
    entry.sent_at = timezone.now()
    entry.next_attempt_at = None
    entry.encrypted_content = ""
    entry.save(update_fields=["attempts", "status", "last_error", "sent_at", "next_attempt_at", "encrypted_content", "updated_at"])
    return True
