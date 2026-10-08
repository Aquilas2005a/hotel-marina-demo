import hashlib
import hmac
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.conf import settings


class FedaPayError(Exception):
    """Erreur exploitable côté interface sans exposer les secrets du fournisseur."""


def verify_webhook(raw_body, signature_header, endpoint_secret, *, tolerance_seconds=300, now=None):
    """Vérifie X-FEDAPAY-SIGNATURE sur le corps brut puis décode l’événement.

    Format documenté par le SDK officiel : t=<epoch>,s=<HMAC-SHA256>. La
    signature porte sur ``<epoch>.<corps brut>`` et sa validité temporelle
    est contrôlée avant toute lecture des données comme preuve de paiement.
    """
    if not endpoint_secret:
        raise FedaPayError("Le secret de point de terminaison webhook FedaPay n’est pas configuré.")
    if not signature_header or len(signature_header) > 2048:
        raise FedaPayError("En-tête de signature FedaPay absent ou invalide.")
    fields = {}
    for part in signature_header.split(","):
        key, separator, value = part.strip().partition("=")
        if separator and key:
            fields.setdefault(key, []).append(value)
    try:
        timestamp = int(fields.get("t", [""])[0])
    except (TypeError, ValueError) as exc:
        raise FedaPayError("Horodatage de webhook invalide.") from exc
    current_time = int(time.time() if now is None else now)
    if abs(current_time - timestamp) > tolerance_seconds:
        raise FedaPayError("Le webhook FedaPay a expiré.")
    expected = hmac.new(endpoint_secret.encode("utf-8"), str(timestamp).encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in fields.get("s", [])):
        raise FedaPayError("Signature du webhook FedaPay invalide.")
    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FedaPayError("Corps JSON du webhook FedaPay invalide.") from exc
    if not isinstance(event, dict):
        raise FedaPayError("Format de l’événement FedaPay invalide.")
    return event


def _api_base():
    if settings.FEDAPAY_ENVIRONMENT == "sandbox":
        return "https://sandbox-api.fedapay.com/v1"
    if settings.FEDAPAY_ENVIRONMENT == "live":
        return "https://api.fedapay.com/v1"
    raise FedaPayError("L’environnement FedaPay doit être sandbox ou live.")


def _request(method, path, payload=None):
    if not settings.FEDAPAY_ENABLED:
        raise FedaPayError("FedaPay n’est pas configuré avec une clé secrète active.")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{_api_base()}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {settings.FEDAPAY_SECRET_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        # Le contenu fournisseur peut inclure des détails sensibles : ne pas le renvoyer au navigateur.
        raise FedaPayError(f"FedaPay a refusé la requête (HTTP {exc.code}).") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise FedaPayError("FedaPay est momentanément inaccessible.") from exc


def _unwrap(data):
    if isinstance(data, dict):
        for key in ("transaction", "v1/transaction"):
            if isinstance(data.get(key), dict):
                return data[key]
    return data


def create_checkout(payable, callback_url, reference_field):
    name_parts = payable.guest_name.split(maxsplit=1)
    room = getattr(payable, "room", None)
    description = f"Séjour {payable.reference} — {room.name}" if room else f"Commande restaurant {payable.reference}"
    customer = {
        "email": payable.guest_email,
        "firstname": name_parts[0],
        "lastname": name_parts[1] if len(name_parts) > 1 else name_parts[0],
    }
    transaction = _unwrap(
        _request(
            "POST",
            "/transactions",
            {
                "description": description,
                "amount": payable.total_price,
                "currency": {"iso": "XOF"},
                "callback_url": callback_url,
                "custom_metadata": {reference_field: payable.reference},
                "customer": customer,
            },
        )
    )
    transaction_id = str(transaction.get("id", "")) if isinstance(transaction, dict) else ""
    if not transaction_id.isdigit():
        raise FedaPayError("La réponse FedaPay ne contient pas de numéro de transaction valide.")
    token_data = _request("POST", f"/transactions/{transaction_id}/token")
    payment_url = token_data.get("url", "") if isinstance(token_data, dict) else ""
    parsed = urlparse(payment_url)
    if parsed.scheme != "https" or not parsed.hostname or not (parsed.hostname == "fedapay.com" or parsed.hostname.endswith(".fedapay.com")):
        raise FedaPayError("FedaPay n’a pas fourni de lien de paiement sécurisé valide.")
    return transaction_id, payment_url


def retrieve_transaction(transaction_id):
    if not str(transaction_id).isdigit():
        raise FedaPayError("Numéro de transaction invalide.")
    data = _unwrap(_request("GET", f"/transactions/{transaction_id}"))
    if not isinstance(data, dict):
        raise FedaPayError("FedaPay a renvoyé une réponse invalide.")
    return data
