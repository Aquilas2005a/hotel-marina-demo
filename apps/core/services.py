from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from django.utils.crypto import get_random_string

from .audit import record_business_event
from .models import Invoice, MenuItem, MenuOption, PromotionCode, RefundRecord, Reservation, ReservationPaymentRecord, RestaurantOrder, RestaurantOrderLine, RestaurantStockMovement, RestaurantTable, Room, RoomBlock, RoomFee, RoomRate, RoomUnit


def get_available_room_units(room, arrival, departure, *, lock=False, exclude_reservation_id=None):
    """Retourne les unités actives libres pour toute la période demandée.

    Une réservation active historique sans unité affectée bloque prudemment
    la catégorie jusqu’à correction manuelle dans l’administration.
    """
    if departure <= arrival:
        return RoomUnit.objects.none()
    units = RoomUnit.objects.filter(room=room, is_active=True)
    if lock:
        units = units.select_for_update()
    occupied = Reservation.objects.filter(
        room=room,
        status__in=["pending", "confirmed", "checked_in"],
        arrival__lt=departure,
        departure__gt=arrival,
    )
    if exclude_reservation_id is not None:
        occupied = occupied.exclude(pk=exclude_reservation_id)
    if occupied.filter(room_unit__isnull=True).exists():
        return RoomUnit.objects.none()
    occupied_unit_ids = occupied.values_list("room_unit_id", flat=True)
    blocked_unit_ids = RoomBlock.objects.filter(
        unit__room=room,
        unit__is_active=True,
        is_active=True,
        start_date__lt=departure,
        end_date__gt=arrival,
    ).values_list("unit_id", flat=True)
    return units.exclude(pk__in=occupied_unit_ids).exclude(pk__in=blocked_unit_ids)


def quote_room_stay(room, arrival, departure):
    """Calcule le prix total nuit par nuit, y compris frais de séjour configurés."""
    return room_stay_breakdown(room, arrival, departure)["total"]


def room_stay_breakdown(room, arrival, departure):
    """Retourne le détail de calcul utilisé pour figer le devis à la réservation."""
    if departure <= arrival:
        raise ValidationError("La date de départ doit être après la date d’arrivée.")
    rates = list(RoomRate.objects.filter(
        room=room,
        is_active=True,
        start_date__lt=departure,
        end_date__gt=arrival,
    ).order_by("start_date"))
    stay_nights = (departure - arrival).days
    arrival_rules = [rate for rate in rates if rate.start_date <= arrival < rate.end_date]
    for rate in arrival_rules:
        if stay_nights < rate.minimum_nights:
            raise ValidationError(f"Le tarif « {rate.label} » impose un séjour minimum de {rate.minimum_nights} nuit(s).")
        if rate.maximum_nights and stay_nights > rate.maximum_nights:
            raise ValidationError(f"Le tarif « {rate.label} » autorise au maximum {rate.maximum_nights} nuit(s).")
        if rate.arrival_weekdays and arrival.weekday() not in rate.arrival_weekdays:
            raise ValidationError(f"Le tarif « {rate.label} » n’autorise pas une arrivée ce jour-là.")
        if rate.departure_weekdays and departure.weekday() not in rate.departure_weekdays:
            raise ValidationError(f"Le tarif « {rate.label} » n’autorise pas un départ ce jour-là.")

    base_total = 0
    current = arrival
    while current < departure:
        matching = [
            rate for rate in rates
            if rate.start_date <= current < rate.end_date
            and (not rate.weekdays or current.weekday() in rate.weekdays)
        ]
        if len(matching) > 1:
            raise ValidationError("Plusieurs tarifs actifs couvrent une même nuit. Corrige les périodes dans l’administration.")
        rate = matching[0] if matching else None
        nightly_price = rate.price_per_night if rate else room.price_per_night
        if rate and rate.discount_percent:
            nightly_price = int((Decimal(nightly_price) * (Decimal(100) - rate.discount_percent) / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        base_total += nightly_price
        current += timedelta(days=1)

    fees = RoomFee.objects.filter(is_active=True).filter(Q(room__isnull=True) | Q(room=room))
    fee_total = Decimal("0")
    for fee in fees:
        if fee.amount_type == "fixed":
            fee_total += fee.amount * (stay_nights if fee.per_night else 1)
        else:
            fee_total += Decimal(base_total) * fee.amount / Decimal("100")
    rounded_fees = int(fee_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return {"base": base_total, "fees": rounded_fees, "total": base_total + rounded_fees}


def _reservation_price_with_promotion(room, arrival, departure, code, *, exclude_reservation_id=None, lock=False):
    quote = room_stay_breakdown(room, arrival, departure)
    if not code:
        return quote, None, ""
    code = str(code).strip().upper()
    promotion_query = PromotionCode.objects
    if lock:
        promotion_query = promotion_query.select_for_update()
    promotion = promotion_query.filter(code=code, is_active=True).first()
    if promotion is None:
        raise ValidationError("Ce code promotionnel est inconnu ou inactif.")
    today = timezone.localdate()
    if not promotion.starts_on <= today <= promotion.ends_on:
        raise ValidationError("Ce code promotionnel n’est pas valable aujourd’hui.")
    if promotion.maximum_uses is not None:
        used = Reservation.objects.filter(promotion=promotion).exclude(status="cancelled")
        if exclude_reservation_id is not None:
            used = used.exclude(pk=exclude_reservation_id)
        if used.count() >= promotion.maximum_uses:
            raise ValidationError("Le nombre d’utilisations prévu pour ce code promotionnel est atteint.")
    if promotion.discount_type == "percent":
        discount = int((Decimal(quote["base"]) * promotion.discount_value / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    else:
        discount = int(promotion.discount_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    discount = min(discount, quote["base"])
    quote["total"] -= discount
    quote["discount"] = discount
    return quote, promotion, promotion.code


@transaction.atomic
def create_reservation(*, form_data, customer=None):
    """Enregistre une demande après revérification de l’inventaire sous verrou."""
    room = _lock_booking_room(form_data["room"].pk)

    arrival = form_data["arrival"]
    departure = form_data["departure"]
    if arrival < timezone.localdate() or departure <= arrival:
        raise ValidationError("Les dates du séjour ne sont pas valides.")
    if form_data["guests"] > room.capacity:
        raise ValidationError("Le nombre de voyageurs dépasse la capacité de la chambre.")

    available_units = get_available_room_units(room, arrival, departure, lock=True)
    room_unit = available_units.first()
    if room_unit is None:
        raise ValidationError("Cette catégorie n’est plus disponible pour ces dates.")

    quote, promotion, promo_code = _reservation_price_with_promotion(
        room, arrival, departure, form_data.get("promo_code"), lock=True,
    )
    return Reservation.objects.create(
        reference=get_random_string(12, allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789"),
        room=room,
        room_unit=room_unit,
        customer=customer,
        guest_name=form_data["guest_name"],
        guest_email=form_data["guest_email"],
        guest_phone=form_data.get("guest_phone", ""),
        arrival=arrival,
        departure=departure,
        guests=form_data["guests"],
        total_price=quote["total"],
        fees_amount=quote["fees"],
        promotion=promotion,
        promotion_code_used=promo_code,
        discount_amount=quote.get("discount", 0),
    )


def _lock_booking_room(room_id):
    """Sérialise les écritures d’inventaire sur MariaDB et SQLite.

    SQLite ignore SELECT FOR UPDATE. Une mise à jour sans changement de valeur
    prend son verrou d’écriture jusqu’à la fin de la transaction, ce qui évite
    deux affectations simultanées dans le mode mono-hôte de démonstration.
    """
    try:
        if connection.vendor == "sqlite":
            locked = Room.objects.filter(pk=room_id, is_available=True).update(units_total=F("units_total"))
            if not locked:
                raise Room.DoesNotExist
            return Room.objects.get(pk=room_id, is_available=True)
        return Room.objects.select_for_update().get(pk=room_id, is_available=True)
    except Room.DoesNotExist as exc:
        raise ValidationError("Cette chambre n’est plus proposée.") from exc


@transaction.atomic
def modify_pending_reservation(*, reservation_id, customer, form_data):
    """Modifie une demande non payée, puis revalide et réaffecte son unité."""
    try:
        reservation = Reservation.objects.select_for_update().get(pk=reservation_id, customer=customer)
    except Reservation.DoesNotExist as exc:
        raise ValidationError("Cette demande n’existe plus.") from exc
    if reservation.status != "pending" or reservation.payment_status in {"pending", "paid", "refund_pending", "refunded"}:
        raise ValidationError("Seule une demande en attente et non payée peut être modifiée depuis le compte client.")

    room = _lock_booking_room(form_data["room"].pk)
    arrival, departure = form_data["arrival"], form_data["departure"]
    guests = form_data["guests"]
    if arrival < timezone.localdate() or departure <= arrival:
        raise ValidationError("Les dates du séjour ne sont pas valides.")
    if guests > room.capacity:
        raise ValidationError("Le nombre de voyageurs dépasse la capacité de la chambre.")
    unit = get_available_room_units(
        room, arrival, departure, lock=True, exclude_reservation_id=reservation.pk,
    ).first()
    if unit is None:
        raise ValidationError("Aucune chambre physique de cette catégorie n’est disponible pour ces dates.")

    reservation.room = room
    reservation.room_unit = unit
    reservation.arrival = arrival
    reservation.departure = departure
    reservation.guests = guests
    reservation.guest_name = form_data["guest_name"]
    reservation.guest_email = form_data["guest_email"]
    reservation.guest_phone = form_data.get("guest_phone", "")
    quote, promotion, promo_code = _reservation_price_with_promotion(
        room, arrival, departure, form_data.get("promo_code"), exclude_reservation_id=reservation.pk, lock=True,
    )
    reservation.total_price = quote["total"]
    reservation.fees_amount = quote["fees"]
    reservation.promotion = promotion
    reservation.promotion_code_used = promo_code
    reservation.discount_amount = quote.get("discount", 0)
    reservation.save(update_fields=[
        "room", "room_unit", "arrival", "departure", "guests", "guest_name",
        "guest_email", "guest_phone", "total_price", "fees_amount", "promotion", "promotion_code_used", "discount_amount",
    ])
    return reservation


def _lock_reservation(reference):
    try:
        if connection.vendor == "sqlite":
            # SQLite ne prend pas en charge SELECT FOR UPDATE ; cette écriture
            # neutre sérialise les modifications de la ligne jusqu’au commit.
            changed = Reservation.objects.filter(reference=reference).update(total_price=F("total_price"))
            if not changed:
                raise Reservation.DoesNotExist
            return Reservation.objects.get(reference=reference)
        return Reservation.objects.select_for_update().get(reference=reference)
    except Reservation.DoesNotExist as exc:
        raise ValidationError("Cette réservation n’existe plus.") from exc


@transaction.atomic
def modify_staff_reservation(*, reference, actor, form_data):
    if not (actor.is_superuser or actor.has_perm("core.change_reservation")):
        raise ValidationError("Tu n’as pas le droit de modifier cette réservation.")
    reservation = _lock_reservation(reference)
    if reservation.status not in {"pending", "confirmed"}:
        raise ValidationError("Une réservation dont le séjour a commencé ou s’est terminé ne peut plus être modifiée de cette façon.")
    if reservation.payment_status == "pending":
        raise ValidationError("Un paiement est en cours de vérification. Termine-le ou annule-le côté fournisseur avant de modifier le séjour.")
    if reservation.payment_status in {"refund_pending", "refunded"}:
        raise ValidationError("Une réservation en cours de remboursement ne peut pas être modifiée.")

    old_total = reservation.total_price
    old_unit = reservation.room_unit
    room = form_data["room"]
    room_ids = sorted({reservation.room_id, room.pk})
    locked_rooms = {item.pk: item for item in (_lock_booking_room(room_id) for room_id in room_ids)}
    room = locked_rooms[room.pk]
    arrival, departure, guests = form_data["arrival"], form_data["departure"], form_data["guests"]
    if arrival < timezone.localdate() or departure <= arrival:
        raise ValidationError("Les dates du séjour ne sont pas valides.")
    if guests > room.capacity:
        raise ValidationError("Le nombre de voyageurs dépasse la capacité de la chambre.")
    available = get_available_room_units(room, arrival, departure, lock=True, exclude_reservation_id=reservation.pk)
    requested_unit = form_data.get("room_unit")
    unit = available.filter(pk=getattr(requested_unit, "pk", None)).first() if requested_unit else None
    if unit is None:
        unit = available.filter(pk=reservation.room_unit_id).first() if reservation.room_id == room.pk else available.first()
    if unit is None:
        raise ValidationError("Aucune unité n’est libre pour les nouvelles dates et la chambre choisie.")

    quote, promotion, promo_code = _reservation_price_with_promotion(
        room, arrival, departure, form_data.get("promo_code"), exclude_reservation_id=reservation.pk, lock=True,
    )
    amount_received = reservation.manual_payments.aggregate(total=Sum("amount"))["total"] or 0
    if reservation.payment_status == "paid" and quote["total"] != old_total:
        raise ValidationError("Le montant déjà payé ne correspondrait plus au nouveau séjour. Traite d’abord un supplément ou un remboursement.")
    if amount_received and quote["total"] < amount_received:
        raise ValidationError("Le nouveau montant serait inférieur aux paiements déjà reçus. Un remboursement doit être traité d’abord.")
    has_invoice = Invoice.objects.filter(reservation=reservation).exists()
    if has_invoice and any([
        room.pk != reservation.room_id, arrival != reservation.arrival, departure != reservation.departure,
        guests != reservation.guests, form_data["guest_name"] != reservation.guest_name,
        form_data["guest_email"] != reservation.guest_email,
        promo_code != reservation.promotion_code_used,
    ]):
        raise ValidationError("Une facture a déjà été émise ; utilise le déplacement d’unité sans changer ses données de facturation.")

    reservation.room = room
    reservation.room_unit = unit
    reservation.arrival = arrival
    reservation.departure = departure
    reservation.guests = guests
    reservation.guest_name = form_data["guest_name"]
    reservation.guest_email = form_data["guest_email"]
    reservation.guest_phone = form_data.get("guest_phone", "")
    reservation.total_price = quote["total"]
    reservation.fees_amount = quote["fees"]
    reservation.promotion = promotion
    reservation.promotion_code_used = promo_code
    reservation.discount_amount = quote.get("discount", 0)
    if amount_received:
        reservation.payment_status = "paid" if amount_received == quote["total"] else "partially_paid"
    reservation.save(update_fields=[
        "room", "room_unit", "arrival", "departure", "guests", "guest_name", "guest_email",
        "guest_phone", "total_price", "fees_amount", "payment_status",
        "promotion", "promotion_code_used", "discount_amount",
    ])
    record_business_event(
        actor=actor, action="reservation.modifiee_equipe", object_type="Réservation", reference=reservation.reference,
        summary=f"Séjour modifié : {reservation.arrival:%Y-%m-%d}–{reservation.departure:%Y-%m-%d}, unité {unit.code}, total {old_total} → {quote['total']} FCFA",
    )
    if reservation.payment_status == "paid" and not has_invoice:
        issue_invoice(reservation=reservation)
    return reservation


@transaction.atomic
def move_reservation_unit(*, reference, target_unit_id, actor):
    if not (actor.is_superuser or actor.has_perm("core.change_reservation")):
        raise ValidationError("Tu n’as pas le droit de déplacer cette réservation.")
    reservation = _lock_reservation(reference)
    if reservation.status not in {"confirmed", "checked_in"}:
        raise ValidationError("Seule une réservation confirmée ou en cours peut changer d’unité.")
    if reservation.departure <= timezone.localdate():
        raise ValidationError("Le séjour est terminé ; le changement d’unité n’est plus possible.")
    _lock_booking_room(reservation.room_id)
    try:
        target = RoomUnit.objects.select_for_update().get(pk=target_unit_id, room_id=reservation.room_id, is_active=True)
    except RoomUnit.DoesNotExist as exc:
        raise ValidationError("Choisis une unité active de la même catégorie.") from exc
    if target.pk == reservation.room_unit_id:
        raise ValidationError("Cette réservation est déjà affectée à cette unité.")
    if target.housekeeping_status not in {"clean", "inspected"}:
        raise ValidationError("L’unité choisie doit être propre et prête.")
    occupancy_start = max(timezone.localdate(), reservation.arrival)
    overlaps = Reservation.objects.filter(
        room_unit=target, status__in=["pending", "confirmed", "checked_in"],
        arrival__lt=reservation.departure, departure__gt=occupancy_start,
    ).exclude(pk=reservation.pk)
    blocked = RoomBlock.objects.filter(
        unit=target, is_active=True, start_date__lt=reservation.departure,
        end_date__gt=occupancy_start,
    ).exists()
    if overlaps.exists() or blocked:
        raise ValidationError("L’unité choisie n’est pas libre jusqu’au départ.")
    old_unit = reservation.room_unit
    reservation.room_unit = target
    reservation.save(update_fields=["room_unit"])
    if old_unit and reservation.status == "checked_in":
        old_unit.housekeeping_status = "needs_cleaning"
        old_unit.save(update_fields=["housekeeping_status"])
    record_business_event(
        actor=actor, action="reservation.unite_deplacee", object_type="Réservation", reference=reservation.reference,
        summary=f"Unité {old_unit.code if old_unit else 'non affectée'} → {target.code}",
    )
    return reservation


@transaction.atomic
def cancel_staff_reservation(*, reference, actor):
    if not (actor.is_superuser or actor.has_perm("core.change_reservation")):
        raise ValidationError("Tu n’as pas le droit d’annuler cette réservation.")
    reservation = _lock_reservation(reference)
    if reservation.status not in {"pending", "confirmed"}:
        raise ValidationError("Seule une réservation en attente ou confirmée peut être annulée depuis la réception.")
    if reservation.arrival <= timezone.localdate():
        raise ValidationError("Le séjour a commencé ou doit commencer aujourd’hui ; traite-le depuis les opérations d’arrivée.")
    if reservation.payment_status == "pending":
        raise ValidationError("Un paiement fournisseur est en cours de vérification. Vérifie-le avant d’annuler.")
    if reservation.payment_status in {"paid", "partially_paid"}:
        reservation.payment_status = "refund_pending"
    reservation.status = "cancelled"
    reservation.save(update_fields=["status", "payment_status"])
    record_business_event(
        actor=actor, action="reservation.annulee_equipe", object_type="Réservation", reference=reservation.reference,
        summary=f"Annulée par la réception · paiement : {reservation.get_payment_status_display()}",
    )
    return reservation


@transaction.atomic
def receive_manual_reservation_payment(*, reference, amount, method, external_reference, actor):
    if not (actor.is_superuser or actor.has_perm("core.change_reservation")):
        raise ValidationError("Tu n’as pas le droit d’enregistrer un paiement.")
    reservation = _lock_reservation(reference)
    if reservation.status in {"cancelled", "checked_out"}:
        raise ValidationError("Cette réservation n’accepte plus de paiement manuel.")
    if reservation.payment_status in {"pending", "paid", "refund_pending", "refunded"}:
        raise ValidationError("Un paiement fournisseur est en cours, déjà payé ou en remboursement.")
    try:
        amount = int(amount)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Saisis un montant valide.") from exc
    if amount <= 0:
        raise ValidationError("Le montant reçu doit être supérieur à zéro.")
    received_before = reservation.manual_payments.aggregate(total=Sum("amount"))["total"] or 0
    outstanding = reservation.total_price - received_before
    if amount > outstanding:
        raise ValidationError(f"Le montant dépasse le solde de {outstanding} FCFA.")
    if method not in dict(ReservationPaymentRecord.METHOD_CHOICES):
        raise ValidationError("Choisis un mode de paiement reconnu.")
    receipt_reference = str(external_reference or "").strip()[:100]
    if receipt_reference and reservation.manual_payments.filter(reference=receipt_reference).exists():
        raise ValidationError("Cette référence d’encaissement est déjà enregistrée sur la réservation.")
    record = ReservationPaymentRecord.objects.create(
        reservation=reservation, amount=amount, method=method,
        reference=receipt_reference, received_by=actor,
        received_by_label=actor.get_full_name().strip() or actor.get_username(),
    )
    received_total = received_before + amount
    full_payment = received_total == reservation.total_price
    reservation.payment_status = "paid" if full_payment else "partially_paid"
    update_fields = ["payment_status"]
    if full_payment and reservation.status == "pending":
        reservation.status = "confirmed"
        update_fields.append("status")
    reservation.save(update_fields=update_fields)
    record_business_event(
        actor=actor, action="paiement.manuel_recu", object_type="Réservation", reference=reservation.reference,
        summary=f"{amount} FCFA reçus ({record.get_method_display()}) · cumul {received_total}/{reservation.total_price} FCFA",
    )
    invoice = None
    if full_payment:
        invoice, _created = issue_invoice(reservation=reservation)
    return record, invoice, received_total, reservation.total_price


@transaction.atomic
def transition_reservation(*, reference, actor, target_status):
    """Applique une transition métier de réservation depuis la réception."""
    try:
        reservation = Reservation.objects.select_for_update().select_related("room_unit").get(reference=reference)
    except Reservation.DoesNotExist as exc:
        raise ValidationError("Cette réservation n’existe plus.") from exc
    if not (actor.is_superuser or actor.has_perm("core.change_reservation")):
        raise ValidationError("Tu n’as pas le droit de modifier cette réservation.")
    today = timezone.localdate()

    if target_status == "confirmed":
        if reservation.status != "pending":
            raise ValidationError("Seule une demande en attente peut être confirmée.")
        room = _lock_booking_room(reservation.room_id)
        if reservation.guests > room.capacity:
            raise ValidationError("Le nombre de voyageurs dépasse la capacité de la chambre.")
        overlaps = Reservation.objects.filter(
            room_unit_id=reservation.room_unit_id,
            status__in=["confirmed", "checked_in"],
            arrival__lt=reservation.departure,
            departure__gt=reservation.arrival,
        ).exclude(pk=reservation.pk)
        if overlaps.exists() or RoomBlock.objects.filter(
            unit_id=reservation.room_unit_id, is_active=True,
            start_date__lt=reservation.departure, end_date__gt=reservation.arrival,
        ).exists():
            raise ValidationError("L’unité attribuée n’est plus libre sur ces dates. Modifie l’affectation avant confirmation.")
        reservation.status = "confirmed"
    elif target_status == "checked_in":
        if reservation.status != "confirmed":
            raise ValidationError("Seule une réservation confirmée peut enregistrer une arrivée.")
        if not reservation.arrival <= today < reservation.departure:
            raise ValidationError("L’arrivée ne peut être enregistrée qu’entre les dates du séjour.")
        if reservation.payment_status != "paid" and reservation.guarantee_status not in {"secured", "not_required"}:
            raise ValidationError("Vérifie le paiement ou enregistre la garantie avant l’arrivée.")
        if reservation.room_unit and reservation.room_unit.housekeeping_status not in {"clean", "inspected"}:
            raise ValidationError("La chambre affectée n’est pas marquée propre et prête.")
        reservation.status = "checked_in"
    elif target_status == "checked_out":
        if reservation.status != "checked_in":
            raise ValidationError("Seule une réservation avec arrivée enregistrée peut être clôturée.")
        if today < reservation.arrival:
            raise ValidationError("Le départ ne peut précéder la date d’arrivée.")
        reservation.status = "checked_out"
        if reservation.room_unit:
            reservation.room_unit.housekeeping_status = "needs_cleaning"
            reservation.room_unit.save(update_fields=["housekeeping_status"])
    else:
        raise ValidationError("Cette transition n’est pas autorisée.")

    reservation.save(update_fields=["status"])
    return reservation


@transaction.atomic
def cancel_customer_reservation(*, reference, customer):
    """Annule une demande client future sans prétendre effectuer un remboursement."""
    try:
        reservation = Reservation.objects.select_for_update().get(reference=reference, customer=customer)
    except Reservation.DoesNotExist as exc:
        raise ValidationError("Cette réservation ne figure pas dans ton compte.") from exc
    if reservation.status not in {"pending", "confirmed"} or reservation.arrival <= timezone.localdate():
        raise ValidationError("Cette réservation ne peut plus être annulée depuis le compte client.")
    latest_cancellation_date = timezone.localdate() + timedelta(days=settings.HOTEL_CANCELLATION_NOTICE_DAYS)
    if reservation.status == "confirmed" and reservation.arrival < latest_cancellation_date:
        raise ValidationError(
            f"Une réservation confirmée doit être annulée au moins {settings.HOTEL_CANCELLATION_NOTICE_DAYS} jour(s) avant l’arrivée."
        )
    if reservation.payment_status == "pending":
        raise ValidationError("Un paiement est en cours de vérification. Contacte l’hôtel avant de demander l’annulation.")
    if reservation.payment_status in {"paid", "partially_paid"}:
        reservation.payment_status = "refund_pending"
    reservation.status = "cancelled"
    reservation.save(update_fields=["status", "payment_status"])
    return reservation


@transaction.atomic
def create_restaurant_order(*, form_data, customer=None):
    requested = {item.pk: quantity for item, quantity in form_data["selected_items"]}
    if connection.vendor == "sqlite" and requested:
        # SQLite ne prend pas en charge SELECT FOR UPDATE ; cette écriture neutre
        # sérialise les commandes et corrections de stock au niveau de la base.
        MenuItem.objects.filter(pk__in=requested).update(stock_quantity=F("stock_quantity"))
    current_items = list(MenuItem.objects.select_for_update().filter(pk__in=requested, is_available=True))
    if len(current_items) != len(requested):
        raise ValidationError("Un ou plusieurs articles ne sont plus disponibles. Actualise la carte.")
    by_id = {item.pk: item for item in current_items}
    selected_options = form_data.get("selected_options", {})
    option_ids = [option.pk for options in selected_options.values() for option in options]
    if connection.vendor == "sqlite" and option_ids:
        MenuOption.objects.filter(pk__in=option_ids).update(additional_price=F("additional_price"))
    current_options = {option.pk: option for option in MenuOption.objects.select_for_update().filter(pk__in=option_ids, is_available=True)}
    if len(current_options) != len(set(option_ids)) or any(option.item_id not in requested for option in current_options.values()):
        raise ValidationError("Une option de la commande n’est plus disponible. Actualise la carte.")
    for item in current_items:
        if item.track_stock and item.stock_quantity < requested[item.pk]:
            raise ValidationError(f"Stock insuffisant pour {item.name}.")
    table = None
    if form_data.get("service_type") == "table":
        table_id = getattr(form_data.get("table"), "pk", None)
        if connection.vendor == "sqlite" and table_id:
            RestaurantTable.objects.filter(pk=table_id).update(capacity=F("capacity"))
        table = RestaurantTable.objects.select_for_update().filter(pk=table_id, is_active=True, status="available").first()
        if table is None:
            raise ValidationError("Cette table vient d’être prise. Choisis une autre table disponible.")
    order = RestaurantOrder.objects.create(
        reference=get_random_string(12, allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789"),
        customer=customer,
        guest_name=form_data["guest_name"],
        guest_email=form_data["guest_email"],
        guest_phone=form_data.get("guest_phone", ""),
        service_type=form_data["service_type"],
        room_number=form_data.get("room_number", ""),
        table=table,
        notes=form_data.get("notes", ""),
    )
    lines = []
    for item in current_items:
        options = [current_options[option.pk] for option in selected_options.get(item.pk, [])]
        option_text = ", ".join(option.name for option in options)
        lines.append(RestaurantOrderLine(
            order=order, menu_item=item, item_name=item.name,
            unit_price=item.price + sum(option.additional_price for option in options),
            quantity=requested[item.pk], options=option_text[:300],
        ))
        if item.track_stock:
            item.stock_quantity -= requested[item.pk]
            item.save(update_fields=["stock_quantity"])
            RestaurantStockMovement.objects.create(
                item=item, order=order, delta=-requested[item.pk], reason=f"Commande {order.reference}",
                actor_label="Commande en ligne",
            )
    RestaurantOrderLine.objects.bulk_create(lines)
    if table:
        table.status = "occupied"
        table.save(update_fields=["status"])
    order.total_price = sum(line.unit_price * line.quantity for line in lines)
    order.save(update_fields=["total_price"])
    return order


@transaction.atomic
def transition_restaurant_order(*, reference, target_status, actor=None):
    if connection.vendor == "sqlite":
        RestaurantOrder.objects.filter(reference=reference).update(total_price=F("total_price"))
    try:
        order = RestaurantOrder.objects.select_for_update().select_related("table").get(reference=reference)
    except RestaurantOrder.DoesNotExist as exc:
        raise ValidationError("Cette commande n’existe plus.") from exc
    allowed = {"pending": {"preparing", "cancelled"}, "preparing": {"ready", "cancelled"}, "ready": {"delivered"}}
    if target_status not in allowed.get(order.status, set()):
        raise ValidationError("Cette transition de commande n’est pas autorisée.")
    if target_status == "cancelled":
        if order.payment_status == "pending":
            raise ValidationError("Un paiement est en cours de vérification ; vérifie-le avant d’annuler la commande.")
        if order.payment_status == "paid":
            order.payment_status = "refund_pending"
        for line in order.lines.select_related("menu_item"):
            if line.menu_item.track_stock:
                MenuItem.objects.filter(pk=line.menu_item_id).update(stock_quantity=F("stock_quantity") + line.quantity)
                RestaurantStockMovement.objects.create(
                    item_id=line.menu_item_id, order=order, delta=line.quantity,
                    reason=f"Remise en stock après annulation {order.reference}",
                    actor=actor,
                    actor_label=(actor.get_full_name().strip() or actor.get_username()) if actor else "Système",
                )
    order.status = target_status
    if target_status in {"delivered", "cancelled"} and order.table_id:
        RestaurantTable.objects.filter(pk=order.table_id, status="occupied").update(status="available")
    order.save(update_fields=["status", "payment_status"])
    return order


@transaction.atomic
def adjust_restaurant_stock(*, item_id, delta, reason, actor):
    if not (actor.is_superuser or actor.has_perm("core.change_menuitem")):
        raise ValidationError("Tu n’as pas le droit de corriger le stock du restaurant.")
    reason = str(reason or "").strip()
    if not delta or abs(delta) > 1000 or not reason:
        raise ValidationError("Saisis une variation non nulle et un motif obligatoire.")
    if connection.vendor == "sqlite":
        MenuItem.objects.filter(pk=item_id).update(stock_quantity=F("stock_quantity"))
    try:
        item = MenuItem.objects.select_for_update().get(pk=item_id)
    except MenuItem.DoesNotExist as exc:
        raise ValidationError("Cet article n’existe plus.") from exc
    if not item.track_stock:
        raise ValidationError("Le suivi de stock n’est pas activé pour cet article.")
    previous_quantity = item.stock_quantity
    next_quantity = previous_quantity + delta
    if next_quantity < 0:
        raise ValidationError("Cette correction ferait passer le stock sous zéro.")
    item.stock_quantity = next_quantity
    item.save(update_fields=["stock_quantity"])
    RestaurantStockMovement.objects.create(
        item=item, delta=delta, reason=reason, actor=actor,
        actor_label=actor.get_full_name().strip() or actor.get_username(),
    )
    record_business_event(
        actor=actor, action="restaurant.stock_ajuste", object_type="Article restaurant",
        reference=item.name,
        summary=f"Stock {previous_quantity} → {next_quantity} ({delta:+d}) · Motif : {reason}",
    )
    return item


@transaction.atomic
def set_restaurant_stock_minimum(*, item_id, minimum, actor):
    if not (actor.is_superuser or actor.has_perm("core.change_menuitem")):
        raise ValidationError("Tu n’as pas le droit de modifier les seuils de stock.")
    if connection.vendor == "sqlite":
        MenuItem.objects.filter(pk=item_id).update(stock_quantity=F("stock_quantity"))
    try:
        minimum = int(minimum)
        item = MenuItem.objects.select_for_update().get(pk=item_id, track_stock=True)
    except (TypeError, ValueError, MenuItem.DoesNotExist) as exc:
        raise ValidationError("Article ou seuil de stock invalide.") from exc
    if minimum < 0 or minimum > 100000:
        raise ValidationError("Le seuil doit être compris entre 0 et 100 000 unités.")
    previous = item.stock_minimum
    item.stock_minimum = minimum
    item.save(update_fields=["stock_minimum"])
    record_business_event(
        actor=actor, action="restaurant.seuil_stock_modifie", object_type="Article restaurant", reference=item.name,
        summary=f"Seuil de réapprovisionnement {previous} → {minimum} unité(s)",
    )
    return item


@transaction.atomic
def issue_invoice(*, reservation=None, restaurant_order=None):
    if (reservation is None) == (restaurant_order is None):
        raise ValidationError("Une facture doit être liée à une réservation ou à une commande.")
    if reservation is not None:
        source = reservation
        number_prefix = "FAC"
        description = f"Séjour {reservation.room.name} du {reservation.arrival:%d/%m/%Y} au {reservation.departure:%d/%m/%Y}"
        defaults = _invoice_amount_fields(source.total_price) | {"reservation": reservation, "fees_amount": source.fees_amount, "customer_name": source.guest_name, "customer_email": source.guest_email, "description": description}
        return Invoice.objects.get_or_create(number=f"{number_prefix}-{timezone.localdate().year}-{source.reference}", defaults=defaults)

    source = restaurant_order
    description = f"Commande restaurant {source.reference} — {source.get_service_type_display()}"
    defaults = _invoice_amount_fields(source.total_price) | {"restaurant_order": source, "customer_name": source.guest_name, "customer_email": source.guest_email, "description": description}
    return Invoice.objects.get_or_create(number=f"FAC-{timezone.localdate().year}-{source.reference}", defaults=defaults)


def _invoice_amount_fields(total):
    """Ventile un montant payé taxes incluses sans modifier le total encaissé."""
    from django.conf import settings

    rate = Decimal(str(settings.HOTEL_TAX_RATE))
    subtotal = int((Decimal(total) / (Decimal("1") + rate / Decimal("100"))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) if rate else int(total)
    return {"subtotal": subtotal, "tax_rate": rate, "tax_amount": int(total) - subtotal, "amount": int(total)}


@transaction.atomic
def record_refund(*, source_type, reference, amount, method, external_reference, notes, actor):
    if source_type == "reservation":
        if not (actor.is_superuser or actor.has_perm("core.change_reservation")):
            raise ValidationError("Tu n’as pas le droit de rapprocher ce remboursement.")
        source = Reservation.objects.select_for_update().filter(reference=reference).first()
        if source is None:
            raise ValidationError("Cette réservation n’existe plus.")
        relation = {"reservation": source}
        status_field = "payment_status"
        manual_total = source.manual_payments.aggregate(total=Sum("amount"))["total"] or 0
        expected = manual_total or source.total_price
    elif source_type == "restaurant":
        if not (actor.is_superuser or actor.has_perm("core.change_restaurantorder")):
            raise ValidationError("Tu n’as pas le droit de rapprocher ce remboursement.")
        source = RestaurantOrder.objects.select_for_update().filter(reference=reference).first()
        if source is None:
            raise ValidationError("Cette commande restaurant n’existe plus.")
        relation = {"restaurant_order": source}
        status_field = "payment_status"
        expected = source.total_price
    else:
        raise ValidationError("Type de paiement non reconnu.")
    if getattr(source, status_field) != "refund_pending":
        raise ValidationError("Ce dossier n’a aucun remboursement à rapprocher.")
    try:
        amount = int(amount)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Saisis un montant valide.") from exc
    external_reference = str(external_reference or "").strip()[:120]
    if amount <= 0 or not external_reference:
        raise ValidationError("Le montant et la référence du remboursement sont obligatoires.")
    if method not in dict(RefundRecord.METHOD_CHOICES):
        raise ValidationError("Choisis un mode de remboursement reconnu.")
    records = RefundRecord.objects.filter(**relation)
    if records.filter(reference=external_reference).exists():
        raise ValidationError("Cette référence de remboursement a déjà été enregistrée.")
    already_refunded = records.aggregate(total=Sum("amount"))["total"] or 0
    remaining = expected - already_refunded
    if amount > remaining:
        raise ValidationError(f"Le montant dépasse le remboursement restant de {remaining} FCFA.")
    refund = RefundRecord.objects.create(
        **relation, amount=amount, method=method, reference=external_reference,
        notes=str(notes or "").strip()[:240], processed_by=actor,
        processed_by_label=actor.get_full_name().strip() or actor.get_username(),
    )
    if already_refunded + amount == expected:
        source.payment_status = "refunded"
        source.save(update_fields=[status_field])
    record_business_event(
        actor=actor, action="paiement.remboursement_rapproche", object_type="Réservation" if source_type == "reservation" else "Commande restaurant",
        reference=reference, summary=f"Remboursement constaté {amount} FCFA · cumul {already_refunded + amount}/{expected} · {external_reference}",
    )
    return refund, already_refunded + amount, expected
