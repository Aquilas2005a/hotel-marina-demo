import logging
import secrets
import hashlib
import csv
from datetime import date, timedelta
from datetime import timedelta as duration
from io import BytesIO
from xml.sax.saxutils import escape

from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.models import Group
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.core.cache import cache
from django.core.paginator import Paginator
from django.core.paginator import Paginator
from django.contrib.auth.password_validation import validate_password
from django.db import connection, transaction
from django.db.models import Count, ExpressionWrapper, F, IntegerField, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.views.decorators.csrf import csrf_exempt

from .fedapay import FedaPayError, create_checkout, retrieve_transaction, verify_webhook
from .audit import record_business_event
from .forms import AdminSetupForm, AvailabilitySearchForm, CustomerLoginForm, CustomerRegistrationForm, EmailVerificationCodeForm, EmailVerificationResendForm, HotelReviewForm, HotelSetupForm, ManualReservationPaymentForm, NewsletterSignupForm, RefundReconciliationForm, ReservationForm, RestaurantOrderForm, RestaurantStockAdjustmentForm, RestaurantStockMinimumForm, RoomMoveForm, StaffMemberForm, StaffRoleForm
from .models import CustomerProfile, FedaPayWebhookEvent, HotelAmenity, HotelInterior, HotelProfile, HotelReview, Invoice, MenuItem, MenuOption, NewsletterSubscription, RefundRecord, Reservation, RestaurantOrder, RestaurantOrderLine, RestaurantStockMovement, RestaurantTable, Room, RoomBlock, RoomUnit, Testimonial
from .services import adjust_restaurant_stock, cancel_customer_reservation, cancel_staff_reservation, create_reservation, create_restaurant_order, get_available_room_units, issue_invoice, modify_pending_reservation, modify_staff_reservation, move_reservation_unit, quote_room_stay, receive_manual_reservation_payment, record_refund, set_restaurant_stock_minimum, transition_reservation, transition_restaurant_order
from .roles import provision_staff_roles
from .tokens import account_activation_token
from .notifications import send_transactional_email

logger = logging.getLogger(__name__)


ROOM_SAMPLES = [
    {"name": "Chambre Jardin", "category": "Confort", "description": "Chambre lumineuse avec matières naturelles et coin salon.", "capacity": 2, "units_total": 4, "price_per_night": 48000, "image_url": "https://images.unsplash.com/photo-1611892440504-42a792e24d32?auto=format&fit=crop&w=1000&q=85"},
    {"name": "Suite Lagune", "category": "Suite", "description": "Suite spacieuse pour un séjour tout en douceur.", "capacity": 2, "units_total": 2, "price_per_night": 92000, "image_url": "https://images.unsplash.com/photo-1590490360182-c33d57733427?auto=format&fit=crop&w=1000&q=85"},
    {"name": "Suite Marina", "category": "Prestige", "description": "Suite familiale avec espace de séjour indépendant.", "capacity": 3, "units_total": 1, "price_per_night": 125000, "image_url": "https://images.unsplash.com/photo-1616486338812-3dadae4b4ace?auto=format&fit=crop&w=1000&q=85"},
]

MENU_SAMPLES = [
    {"name": "Assiette du jardin", "category": "Entrées", "description": "Crudités de saison, herbes fraîches et vinaigrette maison.", "price": 3500},
    {"name": "Poisson braisé du jour", "category": "Plats", "description": "Poisson grillé, accompagnement au choix et sauce pimentée servie à part.", "price": 8500},
    {"name": "Poulet façon maison", "category": "Plats", "description": "Poulet rôti aux épices douces, riz parfumé et légumes.", "price": 7000},
    {"name": "Jus de bissap", "category": "Boissons", "description": "Boisson fraîche à l’hibiscus, recette de démonstration.", "price": 1500},
]

INTERIOR_SAMPLES = [
    {"title": "Chambre de démonstration", "image_url": "https://images.unsplash.com/photo-1611892440504-42a792e24d32?auto=format&fit=crop&w=900&q=85", "sort_order": 1},
    {"title": "Salon de démonstration", "image_url": "https://images.unsplash.com/photo-1616486338812-3dadae4b4ace?auto=format&fit=crop&w=900&q=85", "sort_order": 2},
    {"title": "Espace piscine de démonstration", "image_url": "https://images.unsplash.com/photo-1571896349842-33c89424de2d?auto=format&fit=crop&w=900&q=85", "sort_order": 3},
    {"title": "Restaurant de démonstration", "image_url": "https://images.unsplash.com/photo-1414235077428-338989a2e8c0?auto=format&fit=crop&w=900&q=85", "sort_order": 4},
]

AMENITY_SAMPLES = [
    {"name": "Chambres confortables", "description": "Des chambres aménagées pour le repos et la détente.", "icon": "▤"},
    {"name": "Restaurant", "description": "Une carte de démonstration inspirée des saveurs locales.", "icon": "✦"},
    {"name": "Espace bien-être", "description": "Un espace de relaxation pensé pour votre séjour.", "icon": "◉"},
    {"name": "Accueil disponible", "description": "Une équipe à votre écoute pendant votre visite.", "icon": "⌁"},
]

TESTIMONIAL_SAMPLES = [
    {"guest_name": "Adama K.", "quote": "Un séjour agréable, une chambre confortable et un accueil chaleureux.", "source_label": "Avis fictif de démonstration", "rating": 5},
    {"guest_name": "Marc D.", "quote": "Une belle adresse pour découvrir Cotonou. Je reviendrai avec plaisir.", "source_label": "Avis fictif de démonstration", "rating": 5},
    {"guest_name": "Fatou S.", "quote": "Le restaurant et le service en chambre ont rendu mon séjour facile.", "source_label": "Avis fictif de démonstration", "rating": 4},
]


@require_GET
def healthcheck(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "indisponible"}, status=503)
    return JsonResponse({"status": "ok"})


def accueil(request):
    profile = HotelProfile.objects.first()
    if profile is None:
        return redirect("installation")
    context = {
        "hotel": profile,
        "chambres": Room.objects.filter(is_available=True)[:6],
        "interieurs": HotelInterior.objects.filter(is_active=True),
        "equipements": HotelAmenity.objects.filter(is_active=True),
        "avis": Testimonial.objects.filter(is_active=True)[:6],
        "avis_clients": HotelReview.objects.filter(status="published").select_related("reservation", "guest")[:6],
    }
    return render(request, "core/accueil.html", context)


def informations(request):
    return render(request, "core/informations.html", {"hotel": HotelProfile.objects.first()})


def inscription_newsletter(request):
    if request.method != "POST":
        return redirect("accueil")
    form = NewsletterSignupForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Saisissez une adresse e-mail valide pour vous inscrire.")
        return HttpResponseRedirect(f"{reverse('accueil')}#contact")
    email = form.cleaned_data["email"]
    subscription, created = NewsletterSubscription.objects.get_or_create(email=email)
    if subscription.status == "active":
        messages.success(request, "Cette adresse est déjà inscrite à la newsletter.")
        return HttpResponseRedirect(f"{reverse('accueil')}#contact")
    rate_key = "newsletter-signup:" + hashlib.sha256(email.encode("utf-8")).hexdigest()
    if not cache.add(rate_key, True, timeout=60):
        messages.success(request, "Si le service de courriel est disponible, un lien de confirmation vous sera envoyé.")
        return HttpResponseRedirect(f"{reverse('accueil')}#contact")
    if not created:
        subscription.status = "pending"
        subscription.token_version = secrets.token_urlsafe(24)
        subscription.confirmed_at = None
        subscription.unsubscribed_at = None
        subscription.save(update_fields=["status", "token_version", "confirmed_at", "unsubscribed_at", "updated_at"])
    payload = [subscription.pk, subscription.token_version]
    confirmation = signing.dumps(payload, salt="naya-newsletter-confirm")
    url = request.build_absolute_uri(reverse("newsletter_confirmer", kwargs={"token": confirmation}))
    send_transactional_email(
        subject="Confirmez votre inscription à la newsletter",
        body=f"Pour confirmer votre inscription, ouvrez ce lien : {url}\n\nSi vous n’êtes pas à l’origine de cette demande, ignorez ce message.",
        recipient=email,
    )
    messages.success(request, "Un lien de confirmation a été envoyé si le service de courriel est disponible.")
    return HttpResponseRedirect(f"{reverse('accueil')}#contact")


def _newsletter_from_token(token, salt, max_age):
    try:
        subscription_id, token_version = signing.loads(token, salt=salt, max_age=max_age)
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None
    return NewsletterSubscription.objects.filter(pk=subscription_id, token_version=token_version).first()


@require_http_methods(["GET", "POST"])
def confirmer_newsletter(request, token):
    subscription = _newsletter_from_token(token, "naya-newsletter-confirm", 60 * 60 * 48)
    if subscription is None:
        return render(request, "core/newsletter_action.html", {"hotel": HotelProfile.objects.first(), "action": "confirmation", "valid_token": False}, status=400)
    if request.method == "POST" and subscription.status != "active":
        subscription.status = "active"
        subscription.confirmed_at = timezone.now()
        subscription.unsubscribed_at = None
        subscription.save(update_fields=["status", "confirmed_at", "unsubscribed_at", "updated_at"])
        record_business_event(actor=None, actor_label=subscription.email, action="newsletter.confirmee", object_type="Inscription newsletter", reference=subscription.pk, summary="Consentement confirmé depuis le lien envoyé par courriel.")
        unsubscribe_token = signing.dumps([subscription.pk, subscription.token_version], salt="naya-newsletter-unsubscribe")
        unsubscribe_url = request.build_absolute_uri(reverse("newsletter_desinscrire", kwargs={"token": unsubscribe_token}))
        send_transactional_email(
            subject="Votre inscription à la newsletter est confirmée",
            body=f"Votre inscription est confirmée. Vous pouvez vous désinscrire à tout moment ici : {unsubscribe_url}",
            recipient=subscription.email,
        )
    return render(request, "core/newsletter_action.html", {"hotel": HotelProfile.objects.first(), "action": "confirmation", "valid_token": True, "subscription": subscription})


@require_http_methods(["GET", "POST"])
def desinscrire_newsletter(request, token):
    subscription = _newsletter_from_token(token, "naya-newsletter-unsubscribe", 5 * 365 * 24 * 60 * 60)
    if subscription is None:
        return render(request, "core/newsletter_action.html", {"hotel": HotelProfile.objects.first(), "action": "desinscription", "valid_token": False}, status=400)
    if request.method == "POST" and subscription.status != "unsubscribed":
        subscription.status = "unsubscribed"
        subscription.unsubscribed_at = timezone.now()
        subscription.save(update_fields=["status", "unsubscribed_at", "updated_at"])
        record_business_event(actor=None, actor_label=subscription.email, action="newsletter.desinscrite", object_type="Inscription newsletter", reference=subscription.pk, summary="Désinscription demandée depuis le lien personnel.")
    return render(request, "core/newsletter_action.html", {"hotel": HotelProfile.objects.first(), "action": "desinscription", "valid_token": True, "subscription": subscription})


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.view_reservation") or user.has_perm("core.view_restaurantorder")), login_url="admin:login")
def tableau_equipe(request):
    return _rendre_tableau_equipe(request)


def _role_equipe(user):
    """Détermine l’espace métier à partir des droits réellement attribués."""
    if user.is_superuser or user.has_perm("core.change_hotelprofile"):
        return "gestion"
    if user.has_perm("core.view_restaurantorder") and not user.has_perm("core.view_reservation"):
        return "restaurant"
    if user.has_perm("core.view_reservation"):
        return "reception"
    return None


def tableau_gestion(request):
    return _tableau_role(request, "gestion")


def tableau_reception(request):
    return _tableau_role(request, "reception")


def tableau_restaurant(request):
    return _tableau_role(request, "restaurant")


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.view_restaurantorder")), login_url="admin:login")
def commandes_cuisine(request):
    status = request.GET.get("etat", "active")
    allowed_statuses = {value for value, _label in RestaurantOrder.STATUS_CHOICES}
    orders = RestaurantOrder.objects.select_related("table").prefetch_related("lines")
    if status == "active":
        orders = orders.filter(status__in=["pending", "preparing", "ready"])
    elif status in allowed_statuses:
        orders = orders.filter(status=status)
    else:
        status = "active"
        orders = orders.filter(status__in=["pending", "preparing", "ready"])
    paginator = Paginator(orders.order_by("created_at"), 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "core/commandes_cuisine.html", {
        "hotel": HotelProfile.objects.first(), "page_obj": page_obj,
        "status_filter": status, "status_choices": RestaurantOrder.STATUS_CHOICES,
        "actives": RestaurantOrder.objects.filter(status__in=["pending", "preparing", "ready"]).count(),
    })


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.view_restaurantorder")), login_url="admin:login")
def detail_commande_cuisine(request, reference):
    order = get_object_or_404(
        RestaurantOrder.objects.select_related("table", "customer").prefetch_related("lines"),
        reference=reference,
    )
    return render(request, "core/detail_commande_cuisine.html", {
        "hotel": HotelProfile.objects.first(), "order": order,
        "can_change_order": request.user.is_superuser or request.user.has_perm("core.change_restaurantorder"),
    })


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.view_menuitem")), login_url="admin:login")
def inventaire_restaurant(request):
    items = MenuItem.objects.filter(track_stock=True).order_by("name")
    return render(request, "core/inventaire_restaurant.html", {
        "hotel": HotelProfile.objects.first(), "items": items,
        "movements": RestaurantStockMovement.objects.select_related("item", "order", "actor").all()[:100],
        "low_stock_count": items.filter(stock_quantity__lte=F("stock_minimum")).count(),
        "can_adjust": request.user.is_superuser or request.user.has_perm("core.change_menuitem"),
    })


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.view_reservation") or user.has_perm("core.view_restaurantorder")), login_url="admin:login")
def _tableau_role(request, expected_role):
    if _role_equipe(request.user) != expected_role:
        raise PermissionDenied("Cet espace est réservé au rôle correspondant.")
    return _rendre_tableau_equipe(request)


def _rendre_tableau_equipe(request):
    aujourd_hui = timezone.localdate()
    limite = aujourd_hui + timedelta(days=7)
    role = _role_equipe(request.user)
    can_view_reservations = request.user.is_superuser or request.user.has_perm("core.view_reservation")
    can_view_restaurant = request.user.is_superuser or request.user.has_perm("core.view_restaurantorder")
    reservations = Reservation.objects.select_related("room", "room_unit") if can_view_reservations else Reservation.objects.none()
    commandes = RestaurantOrder.objects.prefetch_related("lines") if can_view_restaurant else RestaurantOrder.objects.none()
    total_reservations = reservations.filter(payment_status="paid").aggregate(total=Sum("total_price"))["total"] or 0
    total_commandes = commandes.filter(payment_status="paid").aggregate(total=Sum("total_price"))["total"] or 0
    base_context = {
        "role_equipe": role,
        "role_label": {"gestion": "Gestionnaire hôtelier", "reception": "Réceptionniste", "restaurant": "Équipe restaurant"}.get(role, "Administration"),
        "can_view_reservations": can_view_reservations,
        "can_view_restaurant": can_view_restaurant,
        "can_manage_staff": request.user.is_superuser or request.user.has_perm("core.change_hotelprofile"),
        "can_view_planning": request.user.is_superuser or (request.user.has_perm("core.view_roomunit") and can_view_reservations),
        "can_change_reservations": request.user.is_superuser or request.user.has_perm("core.change_reservation"),
            "can_change_restaurant_orders": request.user.is_superuser or request.user.has_perm("core.change_restaurantorder"),
        "hotel": HotelProfile.objects.first(),
        "today": aujourd_hui,
    }
    if role == "restaurant":
        commandes_actives = commandes.exclude(status__in=["delivered", "cancelled"])
        context = {
            **base_context,
            "commandes_recues": commandes.filter(status="pending").count(),
            "commandes_preparation": commandes.filter(status="preparing").count(),
            "commandes_pretes": commandes.filter(status="ready").count(),
            "commandes_a_traiter": commandes_actives.count(),
            "dernieres_commandes": commandes_actives.order_by("created_at")[:12],
            "plats_indisponibles": MenuItem.objects.filter(is_available=False).count(),
            "articles_stock": MenuItem.objects.filter(track_stock=True).order_by("name")[:50],
        }
        return render(request, "core/tableau_equipe.html", context)

    arrivees = reservations.filter(arrival=aujourd_hui, status="confirmed")
    departs = reservations.filter(departure=aujourd_hui, status="checked_in")
    chambres_occupees = reservations.filter(status__in=["confirmed", "checked_in"], arrival__lte=aujourd_hui, departure__gt=aujourd_hui).values("room_unit_id").distinct().count()
    if role == "reception":
        context = {
            **base_context,
            "demandes_en_attente": reservations.filter(status="pending").count(),
            "arrivees_jour": arrivees.count(),
            "departs_jour": departs.count(),
            "sejours_a_cloturer": departs.select_related("room", "room_unit")[:10],
            "arrivees_semaine": reservations.filter(status="confirmed", arrival__gte=aujourd_hui, arrival__lte=limite).count(),
            "prochaines_reservations": reservations.filter(status="confirmed", arrival__gte=aujourd_hui).order_by("arrival")[:10],
            "dernieres_demandes": reservations.filter(status="pending").order_by("created_at")[:8],
            "factures_emises": Invoice.objects.count(),
            "montant_encaisse": total_reservations,
        }
        return render(request, "core/tableau_equipe.html", context)

    context = {
        **base_context,
        "demandes_en_attente": reservations.filter(status="pending").count(),
        "arrivees_jour": arrivees.count(),
        "departs_jour": departs.count(),
        "sejours_a_cloturer": departs.select_related("room", "room_unit")[:10],
        "arrivees_semaine": reservations.filter(status="confirmed", arrival__gte=aujourd_hui, arrival__lte=limite).count(),
        "commandes_a_traiter": commandes.exclude(status__in=["delivered", "cancelled"]).count(),
        "montant_encaisse": total_reservations + total_commandes,
        "prochaines_reservations": reservations.filter(status="confirmed", arrival__gte=aujourd_hui).order_by("arrival")[:8],
        "dernieres_demandes": reservations.filter(status="pending").order_by("created_at")[:8],
        "dernieres_commandes": commandes.exclude(status__in=["delivered", "cancelled"])[:8],
        "chambres_occupees": chambres_occupees,
        "chambres_a_nettoyer": RoomUnit.objects.filter(is_active=True, housekeeping_status__in=["needs_cleaning", "cleaning"]).count(),
        "chambres_actives": RoomUnit.objects.filter(is_active=True).count(),
        "chambres_bloquees": RoomBlock.objects.filter(is_active=True, start_date__lte=aujourd_hui, end_date__gt=aujourd_hui).count(),
        "factures_emises": Invoice.objects.count(),
        "plats_indisponibles": MenuItem.objects.filter(is_available=False).count(),
    }
    return render(request, "core/tableau_equipe.html", context)


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or (user.has_perm("core.view_roomunit") and user.has_perm("core.view_reservation"))), login_url="admin:login")
def planning_chambres(request):
    aujourd_hui = timezone.localdate()
    try:
        debut = date.fromisoformat(request.GET.get("debut", "")) if request.GET.get("debut") else aujourd_hui
    except ValueError:
        debut = aujourd_hui
        messages.warning(request, "La date saisie n’était pas valide ; le planning commence aujourd’hui.")

    jours = [debut + timedelta(days=offset) for offset in range(14)]
    fin = jours[-1] + timedelta(days=1)
    units = list(RoomUnit.objects.filter(is_active=True).select_related("room").order_by("room__name", "code"))
    unit_ids = [unit.pk for unit in units]
    reservations = list(Reservation.objects.filter(
        room_unit_id__in=unit_ids,
        status__in=["pending", "confirmed", "checked_in"],
        arrival__lt=fin,
        departure__gt=debut,
    ).order_by("arrival"))
    blocks = list(RoomBlock.objects.filter(
        unit_id__in=unit_ids,
        is_active=True,
        start_date__lt=fin,
        end_date__gt=debut,
    ).order_by("start_date"))

    lignes = []
    for unit in units:
        unit_reservations = [reservation for reservation in reservations if reservation.room_unit_id == unit.pk]
        unit_blocks = [block for block in blocks if block.unit_id == unit.pk]
        cellules = []
        for jour in jours:
            reservation = next((item for item in unit_reservations if item.arrival <= jour < item.departure), None)
            block = next((item for item in unit_blocks if item.start_date <= jour < item.end_date), None)
            if reservation and block:
                cellules.append({"kind": "conflict", "title": "Conflit à traiter", "detail": f"{reservation.reference} · {block.get_kind_display()}"})
            elif block:
                cellules.append({"kind": "blocked", "title": block.get_kind_display(), "detail": block.note or "Unité bloquée"})
            elif reservation:
                cellules.append({"kind": "occupied", "title": reservation.get_status_display(), "detail": reservation.guest_name})
            else:
                cellules.append({"kind": "free", "title": "Libre", "detail": "Disponible"})
        lignes.append({"unit": unit, "cells": cellules})

    return render(request, "core/planning_chambres.html", {
        "hotel": HotelProfile.objects.first(),
        "debut": debut,
        "jours": jours,
        "lignes": lignes,
    })


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.change_hotelprofile")), login_url="admin:login")
def rapport_gestion(request):
    today = timezone.localdate()
    default_start = today.replace(day=1)
    try:
        start = date.fromisoformat(request.GET.get("debut", "")) if request.GET.get("debut") else default_start
        end = date.fromisoformat(request.GET.get("fin", "")) if request.GET.get("fin") else today
    except ValueError:
        start, end = default_start, today
        messages.warning(request, "Période invalide ; affichage du mois en cours.")
    if start > end:
        start, end = end, start
        messages.warning(request, "Les dates ont été remises dans l’ordre chronologique.")
    if (end - start).days > 366:
        end = start + timedelta(days=366)
        messages.warning(request, "La période est limitée à 367 jours.")

    period_days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)

    reservations = Reservation.objects.filter(arrival__gte=start, arrival__lte=end).select_related("room").order_by("arrival")
    orders = RestaurantOrder.objects.filter(created_at__date__gte=start, created_at__date__lte=end).order_by("created_at")
    reservation_stats = reservations.aggregate(
        encaisse=Sum("total_price", filter=Q(payment_status="paid")),
    )
    order_stats = orders.aggregate(
        encaisse=Sum("total_price", filter=Q(payment_status="paid")),
    )
    previous_reservations = Reservation.objects.filter(arrival__gte=previous_start, arrival__lte=previous_end)
    previous_orders = RestaurantOrder.objects.filter(created_at__date__gte=previous_start, created_at__date__lte=previous_end)
    previous_stay_revenue = previous_reservations.filter(payment_status="paid").aggregate(total=Sum("total_price"))["total"] or 0
    previous_restaurant_revenue = previous_orders.filter(payment_status="paid").aggregate(total=Sum("total_price"))["total"] or 0
    previous_total_revenue = previous_stay_revenue + previous_restaurant_revenue
    room_categories = list(reservations.filter(payment_status="paid").values("room__category").annotate(
        reservations=Count("pk"), revenue=Sum("total_price"),
    ).order_by("-revenue")[:10])
    top_customers = list(reservations.filter(payment_status="paid").values("guest_name", "guest_email").annotate(
        stays=Count("pk"), revenue=Sum("total_price"),
    ).order_by("-revenue")[:10])
    paid_lines = RestaurantOrderLine.objects.filter(
        order__in=orders.filter(payment_status="paid"),
    ).annotate(line_revenue=ExpressionWrapper(F("unit_price") * F("quantity"), output_field=IntegerField()))
    top_products = list(paid_lines.values("item_name").annotate(
        quantity=Sum("quantity"), revenue=Sum("line_revenue"),
    ).order_by("-revenue")[:10])
    restaurant_categories = list(paid_lines.values("menu_item__category").annotate(
        quantity=Sum("quantity"), revenue=Sum("line_revenue"),
    ).order_by("-revenue")[:10])

    units = set(RoomUnit.objects.filter(is_active=True, room__is_available=True).values_list("pk", flat=True))
    blocks = list(RoomBlock.objects.filter(
        is_active=True, unit_id__in=units, start_date__lt=end + timedelta(days=1), end_date__gt=start,
    ).values("unit_id", "start_date", "end_date"))
    occupied_stays = list(Reservation.objects.filter(
        room_unit_id__in=units, status__in=["confirmed", "checked_in", "checked_out"],
        arrival__lt=end + timedelta(days=1), departure__gt=start,
    ).values("room_unit_id", "arrival", "departure"))
    available_unit_nights = occupied_unit_nights = 0
    for offset in range(period_days):
        day = start + timedelta(days=offset)
        blocked_units = {block["unit_id"] for block in blocks if block["start_date"] <= day < block["end_date"]}
        usable_units = units - blocked_units
        occupied_units = {
            stay["room_unit_id"] for stay in occupied_stays
            if stay["arrival"] <= day < stay["departure"] and stay["room_unit_id"] in usable_units
        }
        available_unit_nights += len(usable_units)
        occupied_unit_nights += len(occupied_units)
    occupancy_percent = round(occupied_unit_nights * 100 / available_unit_nights, 1) if available_unit_nights else None

    stay_revenue_by_day = dict(reservations.filter(payment_status="paid").values("arrival").annotate(total=Sum("total_price")).values_list("arrival", "total"))
    restaurant_revenue_by_day = dict(orders.filter(payment_status="paid").annotate(day=TruncDate("created_at")).values("day").annotate(total=Sum("total_price")).values_list("day", "total"))
    revenue_trend = []
    for offset in range(period_days):
        day = start + timedelta(days=offset)
        stay_revenue = stay_revenue_by_day.get(day, 0) or 0
        restaurant_revenue = restaurant_revenue_by_day.get(day, 0) or 0
        revenue_trend.append({"day": day, "stays": stay_revenue, "restaurant": restaurant_revenue, "total": stay_revenue + restaurant_revenue})
    if period_days > 62:
        weekly = {}
        for point in revenue_trend:
            week_start = start + timedelta(days=((point["day"] - start).days // 7) * 7)
            bucket = weekly.setdefault(week_start, {"day": week_start, "stays": 0, "restaurant": 0, "total": 0})
            for key in ("stays", "restaurant", "total"):
                bucket[key] += point[key]
        revenue_trend = list(weekly.values())
    peak_revenue = max((point["total"] for point in revenue_trend), default=0)
    for point in revenue_trend:
        point["bar_percent"] = round(point["total"] * 100 / peak_revenue) if peak_revenue else 0

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response.write("\ufeff")
        response["Content-Disposition"] = f'attachment; filename="rapport-hotel-{start:%Y%m%d}-{end:%Y%m%d}.csv"'
        response["Cache-Control"] = "private, no-store"
        writer = csv.writer(response)
        writer.writerow(["Type", "Référence", "Date", "Client", "Courriel", "Détail", "État", "Paiement", "Montant FCFA"])

        def safe_cell(value):
            value = str(value or "")
            return "'" + value if value.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"} else value

        for reservation in reservations:
            detail = f"{reservation.room.name} · {reservation.arrival:%d/%m/%Y}–{reservation.departure:%d/%m/%Y}"
            writer.writerow(["Séjour", reservation.reference, reservation.arrival.isoformat(), safe_cell(reservation.guest_name), safe_cell(reservation.guest_email), safe_cell(detail), reservation.get_status_display(), reservation.get_payment_status_display(), reservation.total_price])
        for order in orders:
            writer.writerow(["Restaurant", order.reference, timezone.localtime(order.created_at).date().isoformat(), safe_cell(order.guest_name), safe_cell(order.guest_email), order.get_service_type_display(), order.get_status_display(), order.get_payment_status_display(), order.total_price])
        return response

    return render(request, "core/rapport_gestion.html", {
        "hotel": HotelProfile.objects.first(), "debut": start, "fin": end,
        "nombre_reservations": reservations.count(), "reservations_confirmees": reservations.filter(status="confirmed").count(),
        "reservations_annulees": reservations.filter(status="cancelled").count(),
        "sejours_encaisse": reservation_stats["encaisse"] or 0,
        "nombre_commandes": orders.count(), "commandes_servies": orders.filter(status="delivered").count(),
        "restaurant_encaisse": order_stats["encaisse"] or 0,
        "total_encaisse": (reservation_stats["encaisse"] or 0) + (order_stats["encaisse"] or 0),
        "previous_start": previous_start, "previous_end": previous_end,
        "previous_total_encaisse": previous_total_revenue,
        "previous_reservation_count": previous_reservations.count(),
        "previous_order_count": previous_orders.count(),
        "ecart_total_encaisse": (reservation_stats["encaisse"] or 0) + (order_stats["encaisse"] or 0) - previous_total_revenue,
        "ecart_reservations": reservations.count() - previous_reservations.count(),
        "ecart_commandes": orders.count() - previous_orders.count(),
        "occupation_percent": occupancy_percent, "chambres_nuites_occupees": occupied_unit_nights,
        "chambres_nuites_disponibles": available_unit_nights, "categories_chambres": room_categories,
        "meilleurs_clients": top_customers, "meilleurs_plats": top_products,
        "categories_restaurant": restaurant_categories, "tendance_recettes": revenue_trend,
        "reservations": reservations[:100], "commandes": orders[:100],
    })


def rechercher_chambres(request):
    hotel = HotelProfile.objects.first()
    if hotel is None:
        return redirect("installation")
    form = AvailabilitySearchForm(request.GET or None)
    chambres = []
    if request.GET and form.is_valid():
        arrival = form.cleaned_data["arrival"]
        departure = form.cleaned_data["departure"]
        guests = form.cleaned_data["guests"]
        candidates = Room.objects.filter(is_available=True, capacity__gte=guests)
        if form.cleaned_data["category"]:
            candidates = candidates.filter(category=form.cleaned_data["category"])
        nights = (departure - arrival).days
        for room in candidates:
            free_units = get_available_room_units(room, arrival, departure)
            if free_units.exists():
                room.quoted_total = quote_room_stay(room, arrival, departure)
                room.quoted_nights = nights
                room.quoted_average_per_night = (room.quoted_total + nights - 1) // nights
                room.available_units = free_units.count()
                minimum, maximum = form.cleaned_data["min_price"], form.cleaned_data["max_price"]
                if minimum is not None and room.quoted_average_per_night < minimum:
                    continue
                if maximum is not None and room.quoted_average_per_night > maximum:
                    continue
                chambres.append(room)
        sort_by = form.cleaned_data["sort_by"]
        if sort_by == "price_asc":
            chambres.sort(key=lambda room: (room.quoted_average_per_night, room.name))
        elif sort_by == "price_desc":
            chambres.sort(key=lambda room: (-room.quoted_average_per_night, room.name))
        elif sort_by == "capacity":
            chambres.sort(key=lambda room: (room.capacity, room.quoted_average_per_night, room.name))
    return render(request, "core/recherche.html", {"hotel": hotel, "form": form, "chambres": chambres, "recherche_effectuee": bool(request.GET and form.is_valid())})


def reserver(request):
    hotel = HotelProfile.objects.first()
    if hotel is None:
        return redirect("installation")
    initial = {}
    if request.user.is_authenticated:
        initial = {
            "guest_name": request.user.get_full_name(),
            "guest_email": request.user.email,
            "guest_phone": getattr(getattr(request.user, "customer_profile", None), "phone", ""),
        }
    for key in ("arrival", "departure", "guests", "room"):
        if request.GET.get(key):
            initial[key] = request.GET.get(key)
    form = ReservationForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            reservation = create_reservation(form_data=form.cleaned_data, customer=request.user if request.user.is_authenticated else None)
            record_business_event(
                actor=request.user if request.user.is_authenticated else None,
                actor_label="Client non connecté" if not request.user.is_authenticated else "",
                action="reservation.creee", object_type="Réservation", reference=reservation.reference,
                summary=f"Demande enregistrée pour {reservation.arrival:%Y-%m-%d} au {reservation.departure:%Y-%m-%d}",
            )
            _notify_reservation_received(reservation)
            return redirect("reservation_confirmee", reference=reservation.reference)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "core/reserver.html", {"hotel": hotel, "form": form, "fedapay_enabled": settings.FEDAPAY_ENABLED})


def reservation_confirmee(request, reference):
    reservation = Reservation.objects.select_related("room", "room_unit").filter(reference=reference).first()
    if reservation is None:
        return redirect("accueil")
    if reservation.customer_id:
        if not request.user.is_authenticated or reservation.customer_id != request.user.pk:
            return redirect("connexion")
    return render(request, "core/reservation_confirmee.html", {"hotel": HotelProfile.objects.first(), "reservation": reservation, "invoice": Invoice.objects.filter(reservation=reservation).first(), "fedapay_enabled": settings.FEDAPAY_ENABLED})


def _notify_reservation_received(reservation):
    subject = f"Demande de séjour {reservation.reference}"
    body = render_to_string("emails/reservation_received.txt", {"reservation": reservation, "hotel": HotelProfile.objects.first()})
    send_transactional_email(subject=subject, body=body, recipient=reservation.guest_email)


@require_POST
def initier_paiement(request, reference):
    reservation = get_object_or_404(Reservation, reference=reference)
    if reservation.customer_id and (not request.user.is_authenticated or reservation.customer_id != request.user.pk):
        return redirect("connexion")
    if reservation.status == "cancelled":
        messages.error(request, "Cette réservation a été annulée et ne peut pas être payée.")
        return redirect("reservation_confirmee", reference=reference)
    if reservation.status in {"checked_out"}:
        messages.error(request, "Le séjour est clôturé. Contacte l’hôtel pour toute régularisation de paiement.")
        return redirect("reservation_confirmee", reference=reference)
    if reservation.payment_status == "paid":
        messages.info(request, "Cette réservation est déjà marquée comme payée.")
        return redirect("reservation_confirmee", reference=reference)
    if reservation.payment_status == "partially_paid":
        messages.error(request, "Un acompte a déjà été encaissé par l’hôtel. Le solde doit être réglé à la réception pour éviter un double paiement.")
        return redirect("reservation_confirmee", reference=reference)
    if reservation.payment_status == "pending" and reservation.payment_url:
        return redirect(reservation.payment_url)
    try:
        callback_url = request.build_absolute_uri(reverse("paiement_retour", kwargs={"reference": reference}))
        transaction_id, payment_url = create_checkout(reservation, callback_url, "reservation_reference")
    except FedaPayError as exc:
        messages.error(request, str(exc))
        return redirect("reservation_confirmee", reference=reference)
    reservation.fedapay_transaction_id = transaction_id
    reservation.payment_url = payment_url
    reservation.payment_status = "pending"
    reservation.save(update_fields=["fedapay_transaction_id", "payment_url", "payment_status"])
    return redirect(payment_url)


def retour_paiement(request, reference):
    reservation = get_object_or_404(Reservation, reference=reference)
    if not reservation.fedapay_transaction_id:
        messages.error(request, "Aucune transaction FedaPay n’est associée à cette réservation.")
        return redirect("reservation_confirmee", reference=reference)
    try:
        transaction_data = retrieve_transaction(reservation.fedapay_transaction_id)
        if int(transaction_data.get("amount", -1)) != reservation.total_price:
            raise FedaPayError("Le montant retourné par FedaPay ne correspond pas à la réservation.")
        metadata = transaction_data.get("custom_metadata") or {}
        if not isinstance(metadata, dict):
            raise FedaPayError("Les métadonnées FedaPay sont invalides.")
        if metadata.get("reservation_reference") not in (None, reference):
            raise FedaPayError("La transaction FedaPay ne correspond pas à cette réservation.")
    except (FedaPayError, TypeError, ValueError) as exc:
        messages.error(request, str(exc) or "Impossible de vérifier le paiement auprès de FedaPay.")
        return redirect("reservation_confirmee", reference=reference)

    provider_status = str(transaction_data.get("status", "")).lower()
    if provider_status == "approved":
        payment_was_already_recorded = reservation.payment_status in {"paid", "refund_pending", "refunded"}
        if reservation.status == "cancelled":
            reservation.payment_status = "refund_pending"
            reservation.save(update_fields=["payment_status"])
            if not payment_was_already_recorded:
                record_business_event(
                    actor_label="Retour FedaPay vérifié", action="paiement.apres_annulation",
                    object_type="Réservation", reference=reservation.reference,
                    summary=f"Paiement reçu après annulation · {reservation.total_price} XOF · remboursement à traiter",
                )
            messages.error(request, "Le paiement a été reçu après l’annulation. Un remboursement doit être traité par l’hôtel.")
        else:
            reservation.payment_status = "paid"
            update_fields = ["payment_status"]
            if reservation.status == "pending":
                reservation.status = "confirmed"
                update_fields.append("status")
            reservation.save(update_fields=update_fields)
            if not payment_was_already_recorded:
                record_business_event(
                    actor_label="Retour FedaPay vérifié", action="paiement.confirme",
                    object_type="Réservation", reference=reservation.reference,
                    summary=f"Paiement confirmé · {reservation.total_price} XOF · transaction {reservation.fedapay_transaction_id}",
                )
            invoice, created = issue_invoice(reservation=reservation)
            if created:
                _notify_invoice(request, invoice)
            messages.success(request, "Paiement confirmé par FedaPay. Votre réservation est confirmée.")
    elif provider_status in {"canceled", "cancelled", "declined", "failed"}:
        payment_was_already_failed = reservation.payment_status == "failed"
        reservation.payment_status = "failed"
        reservation.save(update_fields=["payment_status"])
        if not payment_was_already_failed:
            record_business_event(
                actor_label="Retour FedaPay vérifié", action="paiement.echoue",
                object_type="Réservation", reference=reservation.reference,
                summary=f"État fournisseur : {provider_status} · transaction {reservation.fedapay_transaction_id}",
            )
        messages.warning(request, "FedaPay indique que le paiement n’a pas abouti. Vous pouvez réessayer.")
    else:
        messages.info(request, "Le paiement est toujours en attente de confirmation par FedaPay.")
    return redirect("reservation_confirmee", reference=reference)


@csrf_exempt
@require_POST
def fedapay_webhook(request):
    """Reçoit les événements signés et confirme les paiements après relecture API."""
    if len(request.body) > 65536:
        return JsonResponse({"received": False, "error": "payload_too_large"}, status=413)
    try:
        payload = verify_webhook(
            request.body,
            request.headers.get("X-FEDAPAY-SIGNATURE", ""),
            settings.FEDAPAY_WEBHOOK_SECRET,
        )
    except FedaPayError:
        logger.warning("Webhook FedaPay rejeté : signature, horodatage ou format invalide.")
        return JsonResponse({"received": False, "error": "invalid_signature_or_payload"}, status=400)

    entity = payload.get("entity") or payload.get("data") or {}
    if isinstance(entity, str):
        try:
            entity = json.loads(entity)
        except json.JSONDecodeError:
            entity = {}
    if not isinstance(entity, dict):
        entity = {}
    event_id = str(payload.get("id") or "").strip()
    event_type = str(payload.get("name") or payload.get("type") or payload.get("event") or "").strip()
    transaction_id = str(payload.get("object_id") or entity.get("id") or "").strip()
    if not event_id or len(event_id) > 120 or not event_type or len(event_type) > 100:
        return JsonResponse({"received": False, "error": "invalid_event"}, status=400)

    payload_digest = hashlib.sha256(request.body).hexdigest()
    event, created = FedaPayWebhookEvent.objects.get_or_create(
        event_id=event_id,
        defaults={
            "event_type": event_type,
            "transaction_id": transaction_id,
            "payload_sha256": hashlib.sha256(request.body).hexdigest(),
        },
    )
    if not created and event.payload_sha256 != payload_digest:
        return JsonResponse({"received": False, "error": "event_id_collision"}, status=400)
    if event.status in {"processed", "ignored"}:
        return JsonResponse({"received": True, "duplicate": True})
    event.attempts += 1
    event.status = "received"
    event.last_error = ""
    event.save(update_fields=["attempts", "status", "last_error"])

    if not event_type.startswith("transaction.") or not transaction_id.isdigit():
        event.status = "ignored"
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "processed_at"])
        return JsonResponse({"received": True, "ignored": True})

    provider_transaction = entity if all(key in entity for key in ("id", "amount", "status")) else None
    if provider_transaction is None:
        try:
            provider_transaction = retrieve_transaction(transaction_id)
        except FedaPayError:
            event.status = "failed"
            event.last_error = "Vérification de transaction impossible auprès de FedaPay."
            event.save(update_fields=["status", "last_error"])
            return JsonResponse({"received": False, "error": "provider_unavailable"}, status=503)

    if str(provider_transaction.get("id", "")) != transaction_id:
        event.status = "failed"
        event.last_error = "Identifiant de transaction discordant."
        event.save(update_fields=["status", "last_error"])
        return JsonResponse({"received": False, "error": "transaction_mismatch"}, status=400)

    provider_status = str(provider_transaction.get("status", "")).lower()
    metadata = provider_transaction.get("custom_metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    try:
        provider_amount = int(provider_transaction.get("amount", -1))
    except (TypeError, ValueError):
        provider_amount = -1
    invoice_to_notify = None
    with transaction.atomic():
        event = FedaPayWebhookEvent.objects.select_for_update().get(pk=event.pk)
        if event.status in {"processed", "ignored"}:
            return JsonResponse({"received": True, "duplicate": True})
        reservation = Reservation.objects.select_for_update().filter(fedapay_transaction_id=transaction_id).first()
        order = RestaurantOrder.objects.select_for_update().filter(fedapay_transaction_id=transaction_id).first() if reservation is None else None
        if reservation:
            if metadata.get("reservation_reference") != reservation.reference or provider_amount != reservation.total_price:
                event.status = "failed"
                event.last_error = "Référence ou montant discordant avec la réservation."
                event.save(update_fields=["status", "last_error"])
                return JsonResponse({"received": False, "error": "payment_mismatch"}, status=400)
            payable = reservation
        elif order:
            if metadata.get("restaurant_order_reference") != order.reference or provider_amount != order.total_price:
                event.status = "failed"
                event.last_error = "Référence ou montant discordant avec la commande."
                event.save(update_fields=["status", "last_error"])
                return JsonResponse({"received": False, "error": "payment_mismatch"}, status=400)
            payable = order
        else:
            event.status = "ignored"
            event.last_error = "Transaction sans réservation ou commande locale associée."
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "last_error", "processed_at"])
            return JsonResponse({"received": True, "ignored": True})

        if provider_status == "approved":
            payment_was_already_recorded = payable.payment_status in {"paid", "refund_pending", "refunded"}
            if payable.status == "cancelled":
                payable.payment_status = "refund_pending"
                payable.save(update_fields=["payment_status"])
            else:
                payable.payment_status = "paid"
                update_fields = ["payment_status"]
                if isinstance(payable, Reservation) and payable.status == "pending":
                    payable.status = "confirmed"
                    update_fields.append("status")
                payable.save(update_fields=update_fields)
                invoice_to_notify, created = issue_invoice(reservation=payable) if isinstance(payable, Reservation) else issue_invoice(restaurant_order=payable)
                if not created:
                    invoice_to_notify = None
            if not payment_was_already_recorded:
                record_business_event(
                    actor_label="Webhook FedaPay", action="paiement.apres_annulation" if payable.status == "cancelled" else "paiement.confirme",
                    object_type="Réservation" if isinstance(payable, Reservation) else "Commande restaurant",
                    reference=payable.reference, summary=f"Paiement confirmé · {provider_amount} XOF · transaction {transaction_id}",
                )
        elif provider_status in {"canceled", "cancelled", "declined", "failed"}:
            if payable.payment_status not in {"paid", "refund_pending", "refunded"}:
                payable.payment_status = "failed"
                payable.save(update_fields=["payment_status"])
                record_business_event(
                    actor_label="Webhook FedaPay", action="paiement.echoue",
                    object_type="Réservation" if isinstance(payable, Reservation) else "Commande restaurant",
                    reference=payable.reference, summary=f"État fournisseur : {provider_status} · transaction {transaction_id}",
                )
        else:
            event.status = "ignored"
            event.last_error = "Événement reçu sans état de paiement final."
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "last_error", "processed_at"])
            return JsonResponse({"received": True, "ignored": True})

        event.status = "processed"
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "processed_at"])
        if invoice_to_notify:
            transaction.on_commit(lambda: _notify_invoice(request, invoice_to_notify))
    return JsonResponse({"received": True})


@require_POST
def payer_commande(request, reference):
    order = get_object_or_404(RestaurantOrder, reference=reference)
    if order.customer_id and (not request.user.is_authenticated or order.customer_id != request.user.pk):
        return redirect("connexion")
    if order.status == "cancelled":
        messages.error(request, "Cette commande a été annulée.")
        return redirect("commande_confirmee", reference=reference)
    if order.payment_status == "paid":
        messages.info(request, "Cette commande est déjà marquée comme payée.")
        return redirect("commande_confirmee", reference=reference)
    if order.payment_status == "pending" and order.payment_url:
        return redirect(order.payment_url)
    try:
        callback_url = request.build_absolute_uri(reverse("commande_paiement_retour", kwargs={"reference": reference}))
        transaction_id, payment_url = create_checkout(order, callback_url, "restaurant_order_reference")
    except FedaPayError as exc:
        messages.error(request, str(exc))
        return redirect("commande_confirmee", reference=reference)
    order.fedapay_transaction_id = transaction_id
    order.payment_url = payment_url
    order.payment_status = "pending"
    order.save(update_fields=["fedapay_transaction_id", "payment_url", "payment_status"])
    return redirect(payment_url)


def retour_paiement_commande(request, reference):
    order = get_object_or_404(RestaurantOrder, reference=reference)
    if not order.fedapay_transaction_id:
        messages.error(request, "Aucune transaction FedaPay n’est associée à cette commande.")
        return redirect("commande_confirmee", reference=reference)
    try:
        transaction_data = retrieve_transaction(order.fedapay_transaction_id)
        if int(transaction_data.get("amount", -1)) != order.total_price:
            raise FedaPayError("Le montant retourné par FedaPay ne correspond pas à la commande.")
        metadata = transaction_data.get("custom_metadata") or {}
        if metadata.get("restaurant_order_reference") not in (None, reference):
            raise FedaPayError("La transaction FedaPay ne correspond pas à cette commande.")
    except (FedaPayError, TypeError, ValueError) as exc:
        messages.error(request, str(exc) or "Impossible de vérifier le paiement auprès de FedaPay.")
        return redirect("commande_confirmee", reference=reference)

    provider_status = str(transaction_data.get("status", "")).lower()
    if provider_status == "approved":
        payment_was_already_recorded = order.payment_status in {"paid", "refund_pending", "refunded"}
        order.payment_status = "refund_pending" if order.status == "cancelled" else "paid"
        order.save(update_fields=["payment_status"])
        if not payment_was_already_recorded:
            record_business_event(
                actor_label="Retour FedaPay vérifié", action="paiement.apres_annulation" if order.status == "cancelled" else "paiement.confirme",
                object_type="Commande restaurant", reference=order.reference,
                summary=f"Paiement reçu · {order.total_price} XOF · transaction {order.fedapay_transaction_id}",
            )
        if order.status == "cancelled":
            messages.error(request, "Le paiement a été reçu après l’annulation de la commande. Contacte l’hôtel pour le traitement de cette transaction.")
        else:
            invoice, created = issue_invoice(restaurant_order=order)
            if created:
                _notify_invoice(request, invoice)
            messages.success(request, "Paiement de la commande confirmé par FedaPay.")
    elif provider_status in {"canceled", "cancelled", "declined", "failed"}:
        payment_was_already_failed = order.payment_status == "failed"
        order.payment_status = "failed"
        order.save(update_fields=["payment_status"])
        if not payment_was_already_failed:
            record_business_event(
                actor_label="Retour FedaPay vérifié", action="paiement.echoue",
                object_type="Commande restaurant", reference=order.reference,
                summary=f"État fournisseur : {provider_status} · transaction {order.fedapay_transaction_id}",
            )
        messages.warning(request, "Le paiement de la commande n’a pas abouti. Tu peux réessayer.")
    else:
        messages.info(request, "Le paiement de la commande est toujours en attente.")
    return redirect("commande_confirmee", reference=reference)


def restaurant(request):
    hotel = HotelProfile.objects.first()
    if hotel is None:
        return redirect("installation")
    initial = {}
    if request.user.is_authenticated:
        initial = {"guest_name": request.user.get_full_name(), "guest_email": request.user.email}
    form = RestaurantOrderForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            order = create_restaurant_order(form_data=form.cleaned_data, customer=request.user if request.user.is_authenticated else None)
            record_business_event(
                actor=request.user if request.user.is_authenticated else None,
                actor_label="Client non connecté" if not request.user.is_authenticated else "",
                action="restaurant.commande_creee", object_type="Commande restaurant",
                reference=order.reference, summary=f"Commande créée · {order.total_price} FCFA · {order.get_service_type_display()}",
            )
            send_transactional_email(
                subject=f"Commande restaurant {order.reference}",
                body=render_to_string("emails/restaurant_order_received.txt", {"order": order, "hotel": HotelProfile.objects.first()}),
                recipient=order.guest_email,
            )
            return redirect("commande_confirmee", reference=order.reference)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "core/restaurant.html", {"hotel": hotel, "menu_items": MenuItem.objects.filter(is_available=True), "form": form})


def commande_confirmee(request, reference):
    order = get_object_or_404(RestaurantOrder.objects.prefetch_related("lines"), reference=reference)
    if order.customer_id and (not request.user.is_authenticated or order.customer_id != request.user.pk):
        return redirect("connexion")
    return render(request, "core/commande_confirmee.html", {"hotel": HotelProfile.objects.first(), "order": order, "invoice": Invoice.objects.filter(restaurant_order=order).first(), "fedapay_enabled": settings.FEDAPAY_ENABLED})


def _notify_invoice(request, invoice):
    url = request.build_absolute_uri(reverse("consulter_facture", kwargs={"number": invoice.number}))
    send_transactional_email(
        subject=f"Reçu de paiement {invoice.number}",
        body=render_to_string("emails/invoice_paid.txt", {"invoice": invoice, "invoice_url": url, "hotel": HotelProfile.objects.first()}),
        recipient=invoice.customer_email,
    )


def consulter_facture(request, number):
    invoice = get_object_or_404(Invoice.objects.select_related("reservation", "restaurant_order"), number=number)
    source = invoice.reservation or invoice.restaurant_order
    staff_can_view = request.user.is_authenticated and request.user.is_active and request.user.is_staff and request.user.has_perm("core.view_invoice")
    if source.customer_id and (not request.user.is_authenticated or (source.customer_id != request.user.pk and not staff_can_view)):
        return redirect("connexion")
    if source.payment_status != "paid":
        return redirect("accueil")
    return render(request, "core/facture.html", {"hotel": HotelProfile.objects.first(), "invoice": invoice})


def facture_pdf(request, number):
    invoice = get_object_or_404(Invoice.objects.select_related("reservation", "restaurant_order"), number=number)
    source = invoice.reservation or invoice.restaurant_order
    staff_can_view = request.user.is_authenticated and request.user.is_active and request.user.is_staff and request.user.has_perm("core.view_invoice")
    if source.customer_id and (not request.user.is_authenticated or (source.customer_id != request.user.pk and not staff_can_view)):
        return redirect("connexion")
    if source.payment_status != "paid":
        return redirect("accueil")

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, rightMargin=22 * mm, leftMargin=22 * mm, topMargin=24 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    hotel = HotelProfile.objects.first()
    hotel_name = escape(hotel.hotel_name if hotel else "Naya Marina")
    address = escape(hotel.address if hotel and hotel.address else "")
    city = escape(hotel.city if hotel and hotel.city else "")
    contact = escape(hotel.contact_email if hotel and hotel.contact_email else "")
    rows = [
        ["Désignation", "Montant (FCFA)"],
    ]
    if invoice.fees_amount:
        rows.append([escape(invoice.description), f"{invoice.subtotal - invoice.fees_amount:,}".replace(",", " ")])
        rows.append(["Frais de séjour", f"{invoice.fees_amount:,}".replace(",", " ")])
    else:
        rows.append([escape(invoice.description), f"{invoice.subtotal:,}".replace(",", " ")])
    if invoice.tax_rate:
        rows.append([f"Taxe incluse ({invoice.tax_rate} %)", f"{invoice.tax_amount:,}".replace(",", " ")])
    rows.append(["Total payé", f"{invoice.amount:,}".replace(",", " ")])
    table = Table(rows, colWidths=[115 * mm, 48 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173b37")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d9ded8")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f1efe8")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story = [Paragraph(hotel_name, styles["Title"]), Paragraph(" · ".join(filter(None, [address, city, contact])), styles["Normal"]), Spacer(1, 14 * mm), Paragraph("FACTURE / REÇU DE PAIEMENT", styles["Heading2"]), Paragraph(f"Numéro : {escape(invoice.number)}<br/>Émis le : {invoice.issued_at:%d/%m/%Y %H:%M}<br/>Client : {escape(invoice.customer_name)} ({escape(invoice.customer_email)})", styles["Normal"]), Spacer(1, 9 * mm), table, Spacer(1, 10 * mm), Paragraph("Montants en FCFA. La ventilation fiscale reprend le taux configuré lors de l’émission ; elle ne constitue pas une validation de conformité fiscale locale.", styles["Normal"])]
    document.build(story)
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{invoice.number}.pdf"'
    return response


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.change_hotelprofile")), login_url="admin:login")
def personnel(request):
    User = get_user_model()
    form = StaffMemberForm(request.POST or None)
    if request.method == "POST" and request.POST.get("member_action"):
        try:
            member = User.objects.get(pk=request.POST.get("member_id"), is_staff=True, is_superuser=False)
        except (User.DoesNotExist, ValueError, TypeError):
            messages.error(request, "Ce compte employé est introuvable ou ne peut pas être modifié ici.")
            return redirect("personnel")
        if member.pk == request.user.pk:
            messages.error(request, "Tu ne peux pas modifier ou désactiver ton propre compte depuis cet écran.")
            return redirect("personnel")

        action = request.POST.get("member_action")
        if action == "role":
            role_form = StaffRoleForm(request.POST)
            if not role_form.is_valid():
                messages.error(request, "Choisis un rôle valide pour ce compte.")
                return redirect("personnel")
            current_roles = list(member.groups.values_list("name", flat=True))
            if member.is_active and "Gestionnaire hôtelier" in current_roles:
                another_active_manager = User.objects.filter(
                    is_active=True, is_staff=True, is_superuser=False, groups__name="Gestionnaire hôtelier",
                ).exclude(pk=member.pk).exists()
                if not another_active_manager and role_form.cleaned_data["role"] != "Gestionnaire hôtelier":
                    messages.error(request, "Il faut conserver au moins un gestionnaire actif pour administrer l’hôtel.")
                    return redirect("personnel")
            new_role = role_form.cleaned_data["role"]
            with transaction.atomic():
                group, _ = Group.objects.get_or_create(name=new_role)
                member.groups.set([group])
                record_business_event(
                    actor=request.user, action="personnel.role_modifie", object_type="Compte employé",
                    reference=member.username,
                    summary=f"Rôle : {', '.join(current_roles) or 'aucun'} → {new_role}",
                )
            messages.success(request, f"Le rôle de {member.get_full_name() or member.username} a été mis à jour.")
            return redirect("personnel")

        if action in {"activate", "deactivate"}:
            activate = action == "activate"
            current_roles = list(member.groups.values_list("name", flat=True))
            if not activate and member.is_active and "Gestionnaire hôtelier" in current_roles:
                another_active_manager = User.objects.filter(
                    is_active=True, is_staff=True, is_superuser=False, groups__name="Gestionnaire hôtelier",
                ).exclude(pk=member.pk).exists()
                if not another_active_manager:
                    messages.error(request, "Il faut conserver au moins un gestionnaire actif pour administrer l’hôtel.")
                    return redirect("personnel")
            if member.is_active != activate:
                member.is_active = activate
                member.save(update_fields=["is_active"])
                record_business_event(
                    actor=request.user, action="personnel.compte_active" if activate else "personnel.compte_desactive",
                    object_type="Compte employé", reference=member.username,
                    summary="Accès au compte activé." if activate else "Accès au compte désactivé.",
                )
            messages.success(request, f"Le compte de {member.get_full_name() or member.username} a été {'activé' if activate else 'désactivé'}.")
            return redirect("personnel")

        messages.error(request, "Cette action de gestion n’est pas reconnue.")
        return redirect("personnel")

    if request.method == "POST" and form.is_valid():
        user = User(username=form.cleaned_data["username"], email=form.cleaned_data["email"], first_name=form.cleaned_data["first_name"], last_name=form.cleaned_data["last_name"], is_staff=True, is_active=True)
        try:
            validate_password(form.cleaned_data["password"], user)
        except ValidationError as error:
            form.add_error("password", error)
        else:
            user.set_password(form.cleaned_data["password"])
            user.save()
            group, _ = Group.objects.get_or_create(name=form.cleaned_data["role"])
            user.groups.add(group)
            record_business_event(
                actor=request.user, action="personnel.compte_cree", object_type="Compte employé",
                reference=user.username, summary=f"Rôle attribué : {group.name}",
            )
            messages.success(request, f"Le compte du personnel {user.get_full_name() or user.username} a été créé.")
            return redirect("personnel")
    staff = User.objects.filter(is_staff=True, is_superuser=False).prefetch_related("groups").order_by("username")
    for member in staff:
        member.staff_role = next((group.name for group in member.groups.all() if group.name in {"Gestionnaire hôtelier", "Réceptionniste", "Équipe restaurant"}), "")
    return render(request, "core/personnel.html", {"hotel": HotelProfile.objects.first(), "form": form, "staff": staff})


def inscription(request):
    if request.user.is_authenticated:
        return redirect("mon_compte")
    form = CustomerRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user_model = get_user_model()
        with transaction.atomic():
            user = user_model.objects.create_user(
                username=form.cleaned_data["username"],
                email=form.cleaned_data["email"],
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                password=form.cleaned_data["password"],
                is_active=False,
            )
            CustomerProfile.objects.create(user=user, phone=form.cleaned_data["phone"])
        try:
            _send_activation_email(request, user)
        except Exception:
            logger.exception("Envoi du lien de vérification impossible pour le compte %s.", user.pk)
            messages.error(request, "Le compte est créé mais le courriel n’a pas pu partir. Demande un nouveau lien de vérification.")
        return redirect("verification_envoyee")
    return render(request, "core/inscription.html", {"hotel": HotelProfile.objects.first(), "form": form})


def connexion(request):
    if request.user.is_authenticated:
        return redirect("mon_compte")
    form = CustomerLoginForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        return redirect("mon_compte")
    return render(request, "core/connexion.html", {"hotel": HotelProfile.objects.first(), "form": form})


def _send_activation_email(request, user):
    profile = CustomerProfile.objects.get(user=user)
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    profile.email_verification_code_hash = make_password(code)
    profile.email_verification_code_expires_at = timezone.now() + duration(minutes=30)
    profile.save(update_fields=["email_verification_code_hash", "email_verification_code_expires_at"])
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = account_activation_token.make_token(user)
    link = request.build_absolute_uri(reverse("verifier_email", kwargs={"uidb64": uid, "token": token}))
    send_transactional_email(
        subject="Vérifie ton adresse e-mail — Naya Marina",
        body=render_to_string("emails/account_verification.txt", {
            "user": user, "verification_url": link, "code": code, "hotel": HotelProfile.objects.first(),
        }),
        recipient=user.email,
    )


def verification_envoyee(request):
    form = EmailVerificationCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = get_user_model().objects.filter(email__iexact=form.cleaned_data["email"], is_active=False).first()
        profile = CustomerProfile.objects.filter(user=user, email_verified=False).first() if user else None
        code_valid = bool(
            profile
            and profile.email_verification_code_hash
            and profile.email_verification_code_expires_at
            and profile.email_verification_code_expires_at > timezone.now()
            and check_password(form.cleaned_data["code"], profile.email_verification_code_hash)
        )
        if code_valid:
            _activate_customer_account(request, user, profile)
            messages.success(request, "Adresse vérifiée. Ton compte est activé.")
            return redirect("mon_compte")
        form.add_error(None, "Adresse ou code invalide, expiré ou déjà utilisé. Vérifie les informations ou demande un nouveau code.")
    return render(request, "core/verification_envoyee.html", {"hotel": HotelProfile.objects.first(), "form": form})


def _activate_customer_account(request, user, profile):
    user.is_active = True
    user.save(update_fields=["is_active"])
    profile.email_verified = True
    profile.email_verification_code_hash = ""
    profile.email_verification_code_expires_at = None
    profile.save(update_fields=["email_verified", "email_verification_code_hash", "email_verification_code_expires_at"])
    login(request, user)


def verifier_email(request, uidb64, token):
    user = None
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = get_user_model().objects.get(pk=user_id)
    except (TypeError, ValueError, OverflowError, get_user_model().DoesNotExist):
        pass
    if user and not user.is_active and account_activation_token.check_token(user, token):
        profile = CustomerProfile.objects.filter(user=user, email_verified=False).first()
        if profile is None:
            return render(request, "core/verification_invalide.html", {"hotel": HotelProfile.objects.first()}, status=400)
        _activate_customer_account(request, user, profile)
        messages.success(request, "Adresse vérifiée. Ton compte est activé.")
        return redirect("mon_compte")
    return render(request, "core/verification_invalide.html", {"hotel": HotelProfile.objects.first()}, status=400)


def renvoyer_verification(request):
    form = EmailVerificationResendForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = get_user_model().objects.filter(email__iexact=form.cleaned_data["email"], is_active=False).first()
        if user and CustomerProfile.objects.filter(user=user, email_verified=False).exists():
            try:
                _send_activation_email(request, user)
            except Exception:
                logger.exception("Nouvel envoi de vérification impossible pour le compte %s.", user.pk)
        return redirect("verification_envoyee")
    return render(request, "core/renvoyer_verification.html", {"hotel": HotelProfile.objects.first(), "form": form})


def deconnexion(request):
    if request.method == "POST":
        logout(request)
    return redirect("accueil")


@login_required
def mon_compte(request):
    profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
    reservations = Reservation.objects.select_related("room", "room_unit", "hotel_review").filter(customer=request.user)
    orders = RestaurantOrder.objects.filter(customer=request.user).prefetch_related("lines")
    invoices = Invoice.objects.filter(reservation__customer=request.user) | Invoice.objects.filter(restaurant_order__customer=request.user)
    return render(request, "core/mon_compte.html", {"hotel": HotelProfile.objects.first(), "profile": profile, "reservations": reservations, "orders": orders, "invoices": invoices})


@login_required
@require_http_methods(["GET", "POST"])
def deposer_avis(request, reference):
    reservation = get_object_or_404(
        Reservation.objects.select_related("room"),
        reference=reference,
        customer=request.user,
        status="checked_out",
    )
    profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
    if not profile.email_verified:
        messages.error(request, "Vérifiez votre adresse e-mail avant de déposer un avis.")
        return redirect("mon_compte")

    avis_existant = HotelReview.objects.filter(reservation=reservation).first()
    form = HotelReviewForm(request.POST or None, instance=avis_existant)
    if request.method == "POST" and avis_existant is None and form.is_valid():
        avis = form.save(commit=False)
        avis.reservation = reservation
        avis.guest = request.user
        avis.status = "pending"
        avis.save()
        record_business_event(
            actor=request.user, action="avis.client_depose", object_type="Avis hôtelier",
            reference=reservation.reference, summary="Avis déposé et placé en attente de modération.",
        )
        messages.success(request, "Merci. Votre avis sera publié après vérification par l’équipe.")
        return redirect("mon_compte")
    return render(request, "core/deposer_avis.html", {
        "hotel": HotelProfile.objects.first(), "reservation": reservation,
        "avis": avis_existant, "form": form,
    })


@login_required
def modifier_reservation(request, reference):
    reservation = get_object_or_404(Reservation, reference=reference, customer=request.user)
    if reservation.status != "pending" or reservation.payment_status in {"pending", "paid", "refund_pending", "refunded"}:
        messages.error(request, "Cette demande ne peut plus être modifiée en ligne.")
        return redirect("mon_compte")
    form = ReservationForm(request.POST or None, instance=reservation, exclude_reservation_id=reservation.pk)
    if request.method == "POST" and form.is_valid():
        try:
            modify_pending_reservation(
                reservation_id=reservation.pk,
                customer=request.user,
                form_data=form.cleaned_data,
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            record_business_event(
                actor=request.user, action="reservation.modifiee", object_type="Réservation",
                reference=reservation.reference,
                summary=f"Dates mises à jour : {form.cleaned_data['arrival']:%Y-%m-%d} au {form.cleaned_data['departure']:%Y-%m-%d}",
            )
            messages.success(request, "Ta demande a été mise à jour et la disponibilité a été revérifiée.")
            return redirect("mon_compte")
    return render(request, "core/modifier_reservation.html", {
        "hotel": HotelProfile.objects.first(), "reservation": reservation, "form": form,
    })


@login_required
def annuler_reservation(request, reference):
    if request.method == "POST":
        try:
            reservation = cancel_customer_reservation(reference=reference, customer=request.user)
        except ValidationError as error:
            messages.error(request, error.messages[0])
        else:
            record_business_event(
                actor=request.user, action="reservation.annulee", object_type="Réservation",
                reference=reservation.reference, summary=f"Annulation client · paiement : {reservation.get_payment_status_display()}",
            )
            if reservation.payment_status == "refund_pending":
                messages.warning(request, "La réservation est annulée, mais le remboursement reste à traiter par l’hôtel.")
            else:
                messages.success(request, "La réservation a été annulée.")
        return redirect("mon_compte")
    return redirect("mon_compte")


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.change_reservation")), login_url="admin:login")
@require_POST
def action_reservation(request, reference):
    target = request.POST.get("action", "")
    try:
        reservation = transition_reservation(reference=reference, actor=request.user, target_status=target)
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        record_business_event(
            actor=request.user, action="reservation.transition", object_type="Réservation",
            reference=reservation.reference, summary=f"Nouvel état : {reservation.get_status_display()}",
        )
        messages.success(request, f"Réservation {reservation.reference} : {reservation.get_status_display()}.")
    return redirect("tableau_equipe")


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.change_reservation")), login_url="admin:login")
def detail_reservation_equipe(request, reference):
    reservation = get_object_or_404(Reservation.objects.select_related("room", "room_unit"), reference=reference)
    invoice = Invoice.objects.filter(reservation=reservation).first()
    received = reservation.manual_payments.aggregate(total=Sum("amount"))["total"] or 0
    outstanding = max(reservation.total_price - received, 0)
    edit_form = ReservationForm(instance=reservation, staff_mode=True, exclude_reservation_id=reservation.pk)
    move_form = RoomMoveForm(reservation=reservation)
    payment_form = ManualReservationPaymentForm(max_amount=outstanding) if outstanding else None

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "edit":
                edit_form = ReservationForm(request.POST, instance=reservation, staff_mode=True, exclude_reservation_id=reservation.pk)
                if not edit_form.is_valid():
                    raise ValidationError("Corrige les champs signalés avant d’enregistrer.")
                reservation = modify_staff_reservation(reference=reference, actor=request.user, form_data=edit_form.cleaned_data)
                messages.success(request, "Les informations du séjour ont été mises à jour.")
            elif action == "move":
                move_form = RoomMoveForm(request.POST, reservation=reservation)
                if not move_form.is_valid():
                    raise ValidationError("Choisis une unité propre et disponible.")
                reservation = move_reservation_unit(reference=reference, target_unit_id=move_form.cleaned_data["room_unit"].pk, actor=request.user)
                messages.success(request, "L’unité de la réservation a été modifiée.")
            elif action == "payment":
                payment_form = ManualReservationPaymentForm(request.POST, max_amount=outstanding)
                if not payment_form.is_valid():
                    raise ValidationError("Vérifie le montant et le mode d’encaissement.")
                _record, paid_invoice, received, total = receive_manual_reservation_payment(
                    reference=reference, amount=payment_form.cleaned_data["amount"],
                    method=payment_form.cleaned_data["method"], external_reference=payment_form.cleaned_data["reference"],
                    actor=request.user,
                )
                if paid_invoice:
                    _notify_invoice(request, paid_invoice)
                messages.success(request, f"Encaissement enregistré : {received} / {total} FCFA.")
            elif action == "cancel":
                reservation = cancel_staff_reservation(reference=reference, actor=request.user)
                if reservation.payment_status == "refund_pending":
                    messages.warning(request, "Réservation annulée. Le remboursement est à traiter et à rapprocher manuellement.")
                else:
                    messages.success(request, "La réservation a été annulée.")
            else:
                raise ValidationError("Action non reconnue.")
            return redirect("detail_reservation_equipe", reference=reference)
        except ValidationError as error:
            messages.error(request, error.messages[0])

    reservation.refresh_from_db()
    payments = reservation.manual_payments.select_related("received_by")
    return render(request, "core/detail_reservation_equipe.html", {
        "hotel": HotelProfile.objects.first(), "reservation": reservation, "invoice": invoice,
        "received": received, "outstanding": outstanding, "edit_form": edit_form,
        "move_form": move_form, "payment_form": payment_form, "payments": payments,
    })


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.view_invoice")), login_url="admin:login")
def rapprochement_paiements(request):
    transactions_en_attente = []
    for reservation in Reservation.objects.filter(payment_status="pending").exclude(fedapay_transaction_id="").values(
        "reference", "guest_name", "total_price", "fedapay_transaction_id",
    ):
        transactions_en_attente.append({"source_type": "reservation", **reservation})
    for order in RestaurantOrder.objects.filter(payment_status="pending").exclude(fedapay_transaction_id="").values(
        "reference", "guest_name", "total_price", "fedapay_transaction_id",
    ):
        transactions_en_attente.append({"source_type": "restaurant", **order})
    reservations = list(Reservation.objects.filter(payment_status="refund_pending").select_related("room", "room_unit").prefetch_related("manual_payments", "refunds"))
    orders = list(RestaurantOrder.objects.filter(payment_status="refund_pending").prefetch_related("refunds"))
    for reservation in reservations:
        collected = sum(item.amount for item in reservation.manual_payments.all()) or reservation.total_price
        refunded = sum(item.amount for item in reservation.refunds.all())
        reservation.refund_expected = collected
        reservation.refund_due = max(collected - refunded, 0)
        reservation.refund_history = reservation.refunds.all()
    for order in orders:
        refunded = sum(item.amount for item in order.refunds.all())
        order.refund_expected = order.total_price
        order.refund_due = max(order.total_price - refunded, 0)
        order.refund_history = order.refunds.all()
    if request.method == "POST":
        source_type = request.POST.get("source_type", "")
        reference = request.POST.get("source_reference", "")
        source = next((row for row in reservations if row.reference == reference), None) if source_type == "reservation" else next((row for row in orders if row.reference == reference), None)
        if source is None:
            messages.error(request, "Ce dossier n’a plus de remboursement en attente.")
        else:
            form = RefundReconciliationForm(request.POST, max_amount=source.refund_due)
            if form.is_valid():
                try:
                    refund, cumulative, expected = record_refund(
                        source_type=source_type, reference=reference, amount=form.cleaned_data["amount"],
                        method=form.cleaned_data["method"], external_reference=form.cleaned_data["reference"],
                        notes=form.cleaned_data["notes"], actor=request.user,
                    )
                except ValidationError as error:
                    messages.error(request, error.messages[0])
                else:
                    messages.success(request, f"Remboursement rapproché : {cumulative}/{expected} FCFA · référence {refund.reference}.")
                    return redirect("rapprochement_paiements")
            else:
                messages.error(request, "Vérifie le montant, le mode et la référence du remboursement.")
    else:
        form = None
    return render(request, "core/rapprochement_paiements.html", {
        "hotel": HotelProfile.objects.first(), "reservations": reservations, "orders": orders,
        "form": form, "transactions_en_attente": transactions_en_attente,
    })


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.view_invoice")), login_url="admin:login")
@require_POST
def rapprocher_transaction_fedapay(request, source_type, reference):
    if source_type == "reservation":
        if not (request.user.is_superuser or request.user.has_perm("core.change_reservation")):
            raise PermissionDenied
        source = Reservation.objects.filter(reference=reference).first()
        metadata_key = "reservation_reference"
        object_type = "Réservation"
    elif source_type == "restaurant":
        if not (request.user.is_superuser or request.user.has_perm("core.change_restaurantorder")):
            raise PermissionDenied
        source = RestaurantOrder.objects.filter(reference=reference).first()
        metadata_key = "restaurant_order_reference"
        object_type = "Commande restaurant"
    else:
        raise PermissionDenied
    if source is None or not source.fedapay_transaction_id:
        messages.error(request, "Aucune transaction FedaPay n’est associée à ce dossier.")
        return redirect("rapprochement_paiements")
    try:
        transaction_data = retrieve_transaction(source.fedapay_transaction_id)
        if str(transaction_data.get("id", "")) != source.fedapay_transaction_id:
            raise FedaPayError("L’identifiant FedaPay ne correspond pas au dossier.")
        if int(transaction_data.get("amount", -1)) != source.total_price:
            raise FedaPayError("Le montant FedaPay ne correspond pas au dossier.")
        metadata = transaction_data.get("custom_metadata") or {}
        if metadata.get(metadata_key) != source.reference:
            raise FedaPayError("La référence FedaPay ne correspond pas au dossier.")
    except (FedaPayError, TypeError, ValueError) as error:
        messages.error(request, str(error) or "La transaction FedaPay n’a pas pu être vérifiée.")
        return redirect("rapprochement_paiements")
    provider_status = str(transaction_data.get("status", "")).lower()
    if provider_status not in {"approved", "canceled", "cancelled", "declined", "failed"}:
        messages.info(request, "FedaPay indique que cette transaction est encore en attente.")
        return redirect("rapprochement_paiements")
    with transaction.atomic():
        source = (Reservation if source_type == "reservation" else RestaurantOrder).objects.select_for_update().get(pk=source.pk)
        if source.payment_status not in {"paid", "refund_pending", "refunded"}:
            if provider_status == "approved":
                if source.status == "cancelled":
                    source.payment_status = "refund_pending"
                else:
                    source.payment_status = "paid"
                    if source_type == "reservation" and source.status == "pending":
                        source.status = "confirmed"
                source.save(update_fields=["payment_status", "status"] if source_type == "reservation" else ["payment_status"])
                invoice = None
                created = False
                if source.status != "cancelled":
                    invoice, created = issue_invoice(reservation=source) if source_type == "reservation" else issue_invoice(restaurant_order=source)
                record_business_event(
                    actor=request.user, action="paiement.rapproche", object_type=object_type, reference=source.reference,
                    summary=f"Transaction FedaPay vérifiée · {source.total_price} XOF · {source.fedapay_transaction_id}",
                )
                if invoice and created:
                    _notify_invoice(request, invoice)
            else:
                source.payment_status = "failed"
                source.save(update_fields=["payment_status"])
                record_business_event(
                    actor=request.user, action="paiement.rapproche_echec", object_type=object_type, reference=source.reference,
                    summary=f"Statut FedaPay confirmé : {provider_status} · {source.fedapay_transaction_id}",
                )
    messages.success(request, f"Rapprochement terminé : statut FedaPay « {provider_status} ».")
    return redirect("rapprochement_paiements")


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.change_restaurantorder")), login_url="admin:login")
@require_POST
def action_commande_restaurant(request, reference):
    target = request.POST.get("action", "")
    try:
        order = transition_restaurant_order(reference=reference, target_status=target, actor=request.user)
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        record_business_event(
            actor=request.user, action="restaurant.transition", object_type="Commande restaurant",
            reference=order.reference, summary=f"Nouvel état : {order.get_status_display()} · paiement : {order.get_payment_status_display()}",
        )
        messages.success(request, f"Commande {order.reference} : {order.get_status_display()}.")
        if order.payment_status == "refund_pending":
            messages.warning(request, "Le remboursement reste à traiter auprès du fournisseur de paiement.")
    return redirect("tableau_equipe")


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.change_menuitem")), login_url="admin:login")
@require_POST
def action_stock_restaurant(request, item_id):
    form = RestaurantStockAdjustmentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Saisis une variation valide et un motif pour corriger le stock.")
        return redirect("tableau_equipe")
    try:
        item = adjust_restaurant_stock(
            item_id=item_id, delta=form.cleaned_data["delta"],
            reason=form.cleaned_data["reason"], actor=request.user,
        )
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        messages.success(request, f"Stock de {item.name} mis à jour : {item.stock_quantity} unité(s).")
    return redirect("tableau_equipe")


@user_passes_test(lambda user: user.is_active and user.is_staff and (user.is_superuser or user.has_perm("core.change_menuitem")), login_url="admin:login")
@require_POST
def action_seuil_stock_restaurant(request, item_id):
    form = RestaurantStockMinimumForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Saisis un seuil de stock entre zéro et 100 000.")
    else:
        try:
            item = set_restaurant_stock_minimum(item_id=item_id, minimum=form.cleaned_data["minimum"], actor=request.user)
        except ValidationError as error:
            messages.error(request, error.messages[0])
        else:
            messages.success(request, f"Seuil d’alerte de {item.name} mis à jour : {item.stock_minimum} unité(s).")
    return redirect("inventaire_restaurant")


def installation(request, etape=1):
    if HotelProfile.objects.exists():
        return render(request, "core/installation_terminee.html")
    step = max(1, min(4, int(etape)))
    context = {"step": step, "steps": ["Bienvenue", "Vérifications", "Hôtel et développeur", "Compte administrateur"]}

    if step == 1:
        if request.method == "POST":
            request.session["installation_notice_accepted"] = request.POST.get("notice") == "on"
            if request.session["installation_notice_accepted"]:
                return redirect("installation_etape", etape=2)
            context["error"] = "Veuillez confirmer que vous avez lu les informations de cette maquette."
    elif step == 2:
        try:
            connection.ensure_connection()
            context["database_ok"] = True
        except Exception:
            context["database_ok"] = False
        context["python_version"] = __import__("sys").version.split()[0]
        context["django_version"] = __import__("django").get_version()
        if request.method == "POST" and context["database_ok"]:
            return redirect("installation_etape", etape=3)
    elif step == 3:
        form = HotelSetupForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            request.session["hotel_setup"] = form.cleaned_data
            return redirect("installation_etape", etape=4)
        context["form"] = form
    elif step == 4:
        form = AdminSetupForm(request.POST or None)
        if request.method == "POST" and form.is_valid() and request.session.get("installation_notice_accepted"):
            hotel = request.session.get("hotel_setup")
            if not hotel:
                return redirect("installation_etape", etape=3)
            with transaction.atomic():
                HotelProfile.objects.create(**hotel)
                user_model = get_user_model()
                user_model.objects.create_superuser(
                    username=form.cleaned_data["username"],
                    email=form.cleaned_data["email"],
                    password=form.cleaned_data["password"],
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                )
                provision_staff_roles()
                if hotel.get("demo_mode"):
                    rooms = [Room.objects.create(**room) for room in ROOM_SAMPLES]
                    RoomUnit.objects.bulk_create([
                        RoomUnit(room=room, code=f"{unit_number:03d}")
                        for room in rooms
                        for unit_number in range(1, room.units_total + 1)
                    ])
                    sample_menu_items = [MenuItem.objects.create(**item, track_stock=True, stock_quantity=20) for item in MENU_SAMPLES]
                    sample_menu = {item.name: item for item in sample_menu_items}
                    MenuOption.objects.bulk_create([
                        MenuOption(item=sample_menu["Poisson braisé du jour"], name="Sauce pimentée", additional_price=0),
                        MenuOption(item=sample_menu["Poisson braisé du jour"], name="Portion supplémentaire", additional_price=1500),
                        MenuOption(item=sample_menu["Jus de bissap"], name="Grand format", additional_price=500),
                    ])
                    RestaurantTable.objects.bulk_create([
                        RestaurantTable(name=f"Table {number:02d}", capacity=4) for number in range(1, 6)
                    ])
                    HotelInterior.objects.bulk_create([HotelInterior(**item) for item in INTERIOR_SAMPLES])
                    HotelAmenity.objects.bulk_create([HotelAmenity(**item) for item in AMENITY_SAMPLES])
                    Testimonial.objects.bulk_create([Testimonial(**item) for item in TESTIMONIAL_SAMPLES])
            request.session["installation_finished_at"] = timezone.now().isoformat()
            return redirect("installation_terminee")
        context["form"] = form

    return render(request, "core/installation.html", context)


def installation_terminee(request):
    return render(request, "core/installation_terminee.html", {"installed": HotelProfile.objects.exists()})

