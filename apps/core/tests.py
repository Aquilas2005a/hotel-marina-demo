from datetime import date
from decimal import Decimal
import hashlib
import hmac
import json
import re
import time
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from .models import BusinessAuditLog, CustomerProfile, FedaPayWebhookEvent, HotelProfile, Invoice, MenuItem, MenuOption, PromotionCode, RefundRecord, Reservation, ReservationPaymentRecord, RestaurantOrder, RestaurantStockMovement, RestaurantTable, Room, RoomFee, RoomRate, RoomUnit
from .notifications import retry_email_outbox_entry, send_transactional_email
from .roles import provision_staff_roles
from .services import adjust_restaurant_stock, cancel_staff_reservation, create_reservation, create_restaurant_order, issue_invoice, modify_staff_reservation, move_reservation_unit, quote_room_stay, receive_manual_reservation_payment, record_refund, set_restaurant_stock_minimum, transition_restaurant_order
from .views import _send_activation_email


class HotelApplicationFlowTests(TestCase):
    def setUp(self):
        self.hotel = HotelProfile.objects.create(hotel_name="Naya Marina", city="Cotonou", contact_email="hotel@example.test")
        self.room = Room.objects.create(name="Chambre de test", category="Confort", capacity=2, units_total=1, price_per_night=48000)
        self.unit = RoomUnit.objects.create(room=self.room, code="TEST-01")
        self.reservation = Reservation.objects.create(
            reference="TESTRES2026", room=self.room, room_unit=self.unit, guest_name="Invité de test",
            guest_email="guest@example.test", arrival=date(2026, 12, 10), departure=date(2026, 12, 12),
            guests=1, total_price=96000,
        )

    def test_email_delivery_is_logged_without_retaining_message_body(self):
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            entry = send_transactional_email(
                subject="Confirmation", body="Contenu confidentiel", recipient="guest@example.test",
            )
        self.assertEqual(entry.status, "sent")
        self.assertEqual(entry.attempts, 1)
        self.assertEqual(entry.encrypted_content, "")
        self.assertEqual(mail.outbox[-1].body, "Contenu confidentiel")

    def test_email_delivery_failure_is_encrypted_and_can_be_retried(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode("ascii")
        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            EMAIL_OUTBOX_ENCRYPTION_KEY=key,
        ), patch("apps.core.notifications.send_mail", side_effect=[OSError("SMTP unavailable"), 1]):
            entry = send_transactional_email(
                subject="Confirmation", body="Contenu confidentiel", recipient="guest@example.test",
            )
            self.assertEqual(entry.status, "queued")
            self.assertNotIn("Contenu confidentiel", entry.encrypted_content)
            self.assertEqual(entry.attempts, 1)
            entry.next_attempt_at = timezone.now()
            entry.save(update_fields=["next_attempt_at"])
            self.assertTrue(retry_email_outbox_entry(entry))

        entry.refresh_from_db()
        self.assertEqual(entry.status, "sent")
        self.assertEqual(entry.attempts, 2)
        self.assertEqual(entry.encrypted_content, "")

    def test_restaurant_order_prices_options_assigns_table_and_decrements_stock(self):
        item = MenuItem.objects.create(name="Jus test", price=1500, track_stock=True, stock_quantity=4)
        option = MenuOption.objects.create(item=item, name="Grand format", additional_price=500)
        table = RestaurantTable.objects.create(name="T-1", capacity=2)
        order = create_restaurant_order(form_data={
            "selected_items": [(item, 2)], "selected_options": {item.pk: [option]},
            "guest_name": "Client test", "guest_email": "client@example.test",
            "service_type": "table", "table": table,
        })
        line = order.lines.get()
        item.refresh_from_db()
        table.refresh_from_db()
        self.assertEqual(line.unit_price, 2000)
        self.assertEqual(line.options, "Grand format")
        self.assertEqual(order.total_price, 4000)
        self.assertEqual(item.stock_quantity, 2)
        self.assertEqual(table.status, "occupied")
        self.assertEqual(RestaurantStockMovement.objects.filter(order=order, delta=-2).count(), 1)

    def test_restaurant_order_rejects_insufficient_tracked_stock(self):
        item = MenuItem.objects.create(name="Plat épuisé", price=3000, track_stock=True, stock_quantity=1)
        table = RestaurantTable.objects.create(name="T-2", capacity=2)
        with self.assertRaisesMessage(ValidationError, "Stock insuffisant"):
            create_restaurant_order(form_data={
                "selected_items": [(item, 2)], "guest_name": "Client test",
                "guest_email": "client@example.test", "service_type": "table", "table": table,
            })
        self.assertFalse(RestaurantOrder.objects.filter(guest_name="Client test").exists())

    def test_restaurant_page_accepts_a_table_order_with_an_option(self):
        item = MenuItem.objects.create(name="Jus page", price=1500, track_stock=True, stock_quantity=3)
        option = MenuOption.objects.create(item=item, name="Grand format", additional_price=500)
        table = RestaurantTable.objects.create(name="Table page", capacity=4)
        response = self.client.post(reverse("restaurant"), {
            "guest_name": "Client page", "guest_email": "page@example.test", "service_type": "table",
            "table": str(table.pk), f"item_{item.pk}": "2", f"options_{item.pk}": [str(option.pk)],
        })
        order = RestaurantOrder.objects.get(guest_name="Client page")
        item.refresh_from_db()
        table.refresh_from_db()
        self.assertRedirects(response, reverse("commande_confirmee", kwargs={"reference": order.reference}), fetch_redirect_response=False)
        self.assertEqual(order.total_price, 4000)
        self.assertEqual(order.lines.get().options, "Grand format")
        self.assertEqual(item.stock_quantity, 1)
        self.assertEqual(table.status, "occupied")

    def test_restaurant_cancellation_restores_stock_and_releases_table(self):
        item = MenuItem.objects.create(name="Plat annulé", price=2500, track_stock=True, stock_quantity=3)
        table = RestaurantTable.objects.create(name="Table annulée", capacity=2)
        order = create_restaurant_order(form_data={
            "selected_items": [(item, 2)], "guest_name": "Client", "guest_email": "client@example.test",
            "service_type": "table", "table": table,
        })
        transition_restaurant_order(reference=order.reference, target_status="cancelled")
        item.refresh_from_db()
        table.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(item.stock_quantity, 3)
        self.assertEqual(table.status, "available")
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(RestaurantStockMovement.objects.filter(order=order).values_list("delta", flat=True).order_by("delta").first(), -2)
        self.assertEqual(RestaurantStockMovement.objects.filter(order=order).values_list("delta", flat=True).order_by("delta").last(), 2)

    def test_restaurant_stock_adjustment_is_permissioned_and_audited(self):
        provision_staff_roles()
        User = get_user_model()
        restaurant_staff = User.objects.create_user(username="stock_staff", password="S8#rK4!mQ2xP", is_staff=True)
        restaurant_staff.groups.add(Group.objects.get(name="Équipe restaurant"))
        item = MenuItem.objects.create(name="Stock audit", price=500, track_stock=True, stock_quantity=5)

        updated = adjust_restaurant_stock(item_id=item.pk, delta=3, reason="Réception fournisseur", actor=restaurant_staff)
        self.assertEqual(updated.stock_quantity, 8)
        audit = BusinessAuditLog.objects.get(action="restaurant.stock_ajuste", object_reference=item.name)
        self.assertEqual(audit.actor, restaurant_staff)
        self.assertIn("Réception fournisseur", audit.summary)
        self.assertEqual(RestaurantStockMovement.objects.filter(item=item).count(), 1)

        self.client.force_login(restaurant_staff)
        response = self.client.post(reverse("action_stock_restaurant", kwargs={"item_id": item.pk}), {
            "delta": "-2", "reason": "Produit abîmé",
        })
        self.assertRedirects(response, reverse("tableau_equipe"), fetch_redirect_response=False)
        item.refresh_from_db()
        self.assertEqual(item.stock_quantity, 6)
        self.assertEqual(BusinessAuditLog.objects.filter(action="restaurant.stock_ajuste", object_reference=item.name).count(), 2)
        self.assertEqual(RestaurantStockMovement.objects.filter(item=item).count(), 2)

        threshold = set_restaurant_stock_minimum(item_id=item.pk, minimum=7, actor=restaurant_staff)
        self.assertEqual(threshold.stock_minimum, 7)
        inventory = self.client.get(reverse("inventaire_restaurant"))
        self.assertEqual(inventory.status_code, 200)
        self.assertContains(inventory, "Inventaire restaurant")
        self.assertContains(inventory, "Stock audit")
        self.assertEqual(inventory.context["low_stock_count"], 1)

        with self.assertRaisesMessage(ValidationError, "sous zéro"):
            adjust_restaurant_stock(item_id=item.pk, delta=-7, reason="Erreur d’inventaire", actor=restaurant_staff)

    def test_client_account_redirects_to_local_login(self):
        response = self.client.get(reverse("mon_compte"))
        self.assertRedirects(response, "/compte/connexion/?next=/compte/", fetch_redirect_response=False)

    def test_management_report_is_restricted_and_csv_escapes_formulas(self):
        provision_staff_roles()
        User = get_user_model()
        manager = User.objects.create_superuser(username="report_manager", email="report@example.test", password="R8#pW3!nT6zK")
        self.client.force_login(manager)
        params = {"debut": "2026-12-01", "fin": "2026-12-31"}
        response = self.client.get(reverse("rapport_gestion"), params)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.reservation.reference)

        self.reservation.guest_name = '=HYPERLINK("https://example.test")'
        self.reservation.save(update_fields=["guest_name"])
        export = self.client.get(reverse("rapport_gestion"), params | {"format": "csv"})
        self.assertEqual(export.status_code, 200)
        self.assertIn("text/csv", export["Content-Type"])
        self.assertIn("'=HYPERLINK", export.content.decode("utf-8-sig"))
        self.assertEqual(self.client.get(reverse("admin:core_businessauditlog_changelist")).status_code, 200)

        receptionist = User.objects.create_user(username="report_reception", password="R6#pW3!nT6zK", is_staff=True)
        receptionist.groups.add(Group.objects.get(name="Réceptionniste"))
        self.client.force_login(receptionist)
        self.assertFalse(receptionist.has_perm("core.view_businessauditlog"))
        denied = self.client.get(reverse("rapport_gestion"))
        self.assertEqual(denied.status_code, 302)

    def test_fedapay_checkout_stays_pending_until_provider_approval(self):
        with override_settings(FEDAPAY_ENABLED=True), patch("apps.core.views.create_checkout", return_value=("519999", "https://sandbox-process.fedapay.com/test")):
            response = self.client.post(reverse("initier_paiement", kwargs={"reference": self.reservation.reference}))
        self.reservation.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://sandbox-process.fedapay.com/test")
        self.assertEqual(self.reservation.payment_status, "pending")
        self.assertEqual(self.reservation.fedapay_transaction_id, "519999")

    @override_settings(HOTEL_TAX_RATE=Decimal("18.00"))
    def test_paid_invoice_renders_pdf_with_tax_breakdown(self):
        self.reservation.payment_status = "paid"
        self.reservation.save(update_fields=["payment_status"])
        invoice, _ = issue_invoice(reservation=self.reservation)
        self.assertEqual(invoice.amount, 96000)
        self.assertEqual(invoice.tax_rate, Decimal("18.00"))
        self.assertEqual(invoice.subtotal + invoice.tax_amount, invoice.amount)
        response = self.client.get(reverse("facture_pdf", kwargs={"number": invoice.number}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_manager_can_create_an_employee_and_assign_a_role(self):
        provision_staff_roles()
        User = get_user_model()
        manager = User.objects.create_superuser(username="test_manager", email="manager@example.test", password="H3q#vK8w!sT5pR")
        self.client.force_login(manager)
        response = self.client.post(reverse("personnel"), {
            "username": "test_reception", "email": "reception@example.test",
            "first_name": "Test", "last_name": "Réception", "role": "Réceptionniste",
            "password": "wD8#kQ2v!Jm5sR",
        })
        self.assertRedirects(response, reverse("personnel"), fetch_redirect_response=False)
        staff = User.objects.get(username="test_reception")
        self.assertTrue(staff.is_staff)
        self.assertTrue(staff.groups.filter(name="Réceptionniste").exists())
        self.assertTrue(Group.objects.get(name="Réceptionniste").permissions.filter(codename="view_reservation").exists())
        audit = BusinessAuditLog.objects.get(action="personnel.compte_cree", object_reference="test_reception")
        self.assertEqual(audit.actor, manager)
        self.assertIn("Réceptionniste", audit.summary)
        page = self.client.get(reverse("personnel"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Désactiver le compte")
        self.assertContains(page, "Enregistrer le rôle")

        self.client.post(reverse("personnel"), {
            "member_action": "role", "member_id": staff.pk, "role": "Équipe restaurant",
        })
        staff.refresh_from_db()
        self.assertTrue(staff.groups.filter(name="Équipe restaurant").exists())
        self.assertTrue(BusinessAuditLog.objects.filter(action="personnel.role_modifie", object_reference=staff.username).exists())

        self.client.post(reverse("personnel"), {"member_action": "deactivate", "member_id": staff.pk})
        staff.refresh_from_db()
        self.assertFalse(staff.is_active)
        self.client.post(reverse("personnel"), {"member_action": "activate", "member_id": staff.pk})
        staff.refresh_from_db()
        self.assertTrue(staff.is_active)

        sole_manager = User.objects.create_user(username="sole_manager", password="U7#rM9!xQ2wL", is_staff=True)
        sole_manager.groups.add(Group.objects.get(name="Gestionnaire hôtelier"))
        self.client.post(reverse("personnel"), {"member_action": "deactivate", "member_id": sole_manager.pk})
        sole_manager.refresh_from_db()
        self.assertTrue(sole_manager.is_active)

    def test_role_permissions_match_their_operational_pages(self):
        provision_staff_roles()
        self.reservation.status = "confirmed"
        self.reservation.save(update_fields=["status"])
        User = get_user_model()
        reception = User.objects.create_user(username="reception_test", password="A8#qZ6!mR2xP")
        reception.is_staff = True
        reception.save(update_fields=["is_staff"])
        reception.groups.add(Group.objects.get(name="Réceptionniste"))
        restaurant = User.objects.create_user(username="restaurant_test", password="B9#rY7!nS3wQ")
        restaurant.is_staff = True
        restaurant.save(update_fields=["is_staff"])
        restaurant.groups.add(Group.objects.get(name="Équipe restaurant"))
        kitchen_order = RestaurantOrder.objects.create(
            reference="KITCHEN12345", guest_name="Client cuisine", guest_email="kitchen@example.test",
            service_type="takeaway",
        )

        self.client.force_login(reception)
        reception_dashboard = self.client.get(reverse("tableau_equipe"))
        self.assertEqual(reception_dashboard.status_code, 200)
        self.assertContains(reception_dashboard, "ESPACE RÉCEPTIONNISTE")
        self.assertContains(reception_dashboard, "ARRIVÉES AUJOURD’HUI")
        self.assertContains(reception_dashboard, self.reservation.guest_name)
        self.assertEqual(self.client.get(reverse("tableau_reception")).status_code, 200)
        self.assertEqual(self.client.get(reverse("tableau_restaurant")).status_code, 403)
        self.assertEqual(self.client.get(reverse("commandes_cuisine")).status_code, 302)
        self.assertEqual(self.client.get(reverse("planning_chambres")).status_code, 200)
        self.assertEqual(self.client.get(reverse("personnel")).status_code, 302)

        self.client.force_login(restaurant)
        restaurant_dashboard = self.client.get(reverse("tableau_equipe"))
        self.assertEqual(restaurant_dashboard.status_code, 200)
        self.assertContains(restaurant_dashboard, "ESPACE ÉQUIPE RESTAURANT")
        self.assertContains(restaurant_dashboard, "NOUVELLES COMMANDES")
        self.assertContains(restaurant_dashboard, "Ouvrir la file cuisine")
        self.assertNotContains(restaurant_dashboard, self.reservation.guest_name)
        self.assertEqual(self.client.get(reverse("tableau_restaurant")).status_code, 200)
        kitchen_page = self.client.get(reverse("commandes_cuisine"))
        self.assertEqual(kitchen_page.status_code, 200)
        self.assertContains(kitchen_page, "kitchen-board")
        self.assertContains(kitchen_page, kitchen_order.reference)
        ticket_page = self.client.get(reverse("detail_commande_cuisine", kwargs={"reference": kitchen_order.reference}))
        self.assertEqual(ticket_page.status_code, 200)
        self.assertContains(ticket_page, "Imprimer le ticket")
        transition_response = self.client.post(
            reverse("action_commande_restaurant", kwargs={"reference": kitchen_order.reference}),
            {"action": "preparing"},
        )
        self.assertRedirects(transition_response, reverse("tableau_equipe"), fetch_redirect_response=False)
        kitchen_order.refresh_from_db()
        self.assertEqual(kitchen_order.status, "preparing")
        self.assertTrue(BusinessAuditLog.objects.filter(
            actor=restaurant, action="restaurant.transition", object_reference=kitchen_order.reference,
        ).exists())
        self.assertEqual(self.client.get(reverse("tableau_reception")).status_code, 403)
        self.assertEqual(self.client.get(reverse("planning_chambres")).status_code, 302)
        self.assertEqual(self.client.get(reverse("personnel")).status_code, 302)

        manager = User.objects.create_superuser(username="dashboard_manager", email="dashboard@example.test", password="P8#xM4!vR2qL")
        self.client.force_login(manager)
        manager_dashboard = self.client.get(reverse("tableau_gestion"))
        self.assertEqual(manager_dashboard.status_code, 200)
        self.assertContains(manager_dashboard, "UNITÉS OCCUPÉES AUJOURD’HUI")
        self.assertContains(manager_dashboard, "Gérer le personnel")
        self.assertContains(manager_dashboard, "Rapports et exports")

    def test_manager_role_has_tariff_and_reconciliation_access(self):
        provision_staff_roles()
        manager = Group.objects.get(name="Gestionnaire hôtelier")
        self.assertTrue(manager.permissions.filter(codename="add_promotioncode").exists())
        self.assertTrue(manager.permissions.filter(codename="view_refundrecord").exists())
        restaurant = Group.objects.get(name="Équipe restaurant")
        self.assertTrue(restaurant.permissions.filter(codename="view_restaurantstockmovement").exists())

    def test_django_admin_changes_are_written_to_business_audit(self):
        manager = get_user_model().objects.create_superuser(
            username="admin_audit", email="admin-audit@example.test", password="A7#xR2!kM8vP",
        )
        self.client.force_login(manager)
        response = self.client.post(reverse("admin:core_roomrate_add"), {
            "room": self.room.pk, "label": "Tarif admin tracé", "start_date": "2026-12-01",
            "end_date": "2027-01-01", "price_per_night": 80000, "minimum_nights": 1,
            "maximum_nights": "", "weekdays": [], "arrival_weekdays": [], "departure_weekdays": [],
            "discount_percent": 0, "is_active": "on", "_save": "Enregistrer",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(BusinessAuditLog.objects.filter(
            actor=manager, action="admin.objet_ajoute", object_type="Tarif saisonnier",
        ).exists(), msg=f"URL={response.url}; tarif={RoomRate.objects.filter(label='Tarif admin tracé').exists()}; événements={list(BusinessAuditLog.objects.values_list('action', 'object_type', 'summary'))}")

    def test_customer_can_modify_pending_unpaid_reservation(self):
        User = get_user_model()
        customer = User.objects.create_user(username="edit_guest", email="edit@example.test", password="T3#vN8!xQ5mL")
        self.reservation.customer = customer
        self.reservation.save(update_fields=["customer"])
        self.client.force_login(customer)

        response = self.client.post(reverse("modifier_reservation", kwargs={"reference": self.reservation.reference}), {
            "room": self.room.pk,
            "guest_name": "Invité modifié",
            "guest_email": "edit@example.test",
            "guest_phone": "+229 97000000",
            "arrival": "2026-12-12",
            "departure": "2026-12-14",
            "guests": 2,
        })

        self.assertRedirects(response, reverse("mon_compte"), fetch_redirect_response=False)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.guest_name, "Invité modifié")
        self.assertEqual(self.reservation.arrival, date(2026, 12, 12))
        self.assertEqual(self.reservation.total_price, 96000)

    def test_staff_can_record_partial_then_full_reservation_payment(self):
        provision_staff_roles()
        User = get_user_model()
        receptionist = User.objects.create_user(username="payment_reception", password="V4#kR7!mQ2pZ", is_staff=True)
        receptionist.groups.add(Group.objects.get(name="Réceptionniste"))
        first, invoice, received, total = receive_manual_reservation_payment(
            reference=self.reservation.reference, amount=30000, method="cash", external_reference="TICKET-1", actor=receptionist,
        )
        self.assertIsNone(invoice)
        self.assertEqual(received, 30000)
        self.assertEqual(first.reference, "TICKET-1")
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.payment_status, "partially_paid")
        with self.assertRaisesMessage(ValidationError, "déjà enregistrée"):
            receive_manual_reservation_payment(
                reference=self.reservation.reference, amount=1000, method="cash", external_reference="TICKET-1", actor=receptionist,
            )
        with self.assertRaisesMessage(ValidationError, "dépasse le solde"):
            receive_manual_reservation_payment(
                reference=self.reservation.reference, amount=total, method="cash", external_reference="TICKET-OVER", actor=receptionist,
            )
        _second, invoice, received, total = receive_manual_reservation_payment(
            reference=self.reservation.reference, amount=total - received, method="mobile_money", external_reference="MM-2", actor=receptionist,
        )
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.payment_status, "paid")
        self.assertEqual(self.reservation.status, "confirmed")
        self.assertEqual(received, total)
        self.assertTrue(invoice)
        self.assertEqual(Invoice.objects.filter(reservation=self.reservation).count(), 1)
        self.assertEqual(ReservationPaymentRecord.objects.filter(reservation=self.reservation).count(), 2)

    def test_staff_reservation_workspace_allows_edit_and_unit_move(self):
        provision_staff_roles()
        User = get_user_model()
        receptionist = User.objects.create_user(username="workspace_reception", password="V4#kR7!mQ2pZ", is_staff=True)
        receptionist.groups.add(Group.objects.get(name="Réceptionniste"))
        self.reservation.status = "confirmed"
        self.reservation.save(update_fields=["status"])
        self.client.force_login(receptionist)
        url = reverse("detail_reservation_equipe", kwargs={"reference": self.reservation.reference})
        self.assertEqual(self.client.get(url).status_code, 200)
        second_unit = RoomUnit.objects.create(room=self.room, code="TEST-02")
        self.client.post(url, {"action": "edit", "room": self.room.pk, "room_unit": self.unit.pk,
            "guest_name": "Invité corrigé", "guest_email": "guest@example.test", "guest_phone": "",
            "arrival": "2026-12-11", "departure": "2026-12-13", "guests": 1})
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.guest_name, "Invité corrigé")
        move_response = self.client.post(url, {"action": "move", "room_unit": second_unit.pk})
        self.assertRedirects(move_response, url, fetch_redirect_response=False)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.room_unit_id, second_unit.pk)
        self.assertTrue(BusinessAuditLog.objects.filter(action="reservation.unite_deplacee", object_reference=self.reservation.reference).exists())

    def test_staff_reservation_workspace_rejects_users_without_permission(self):
        User = get_user_model()
        user = User.objects.create_user(username="no_permission", password="V4#kR7!mQ2pZ", is_staff=True)
        self.client.force_login(user)
        response = self.client.get(reverse("detail_reservation_equipe", kwargs={"reference": self.reservation.reference}))
        self.assertEqual(response.status_code, 302)

    def test_staff_can_cancel_confirmed_booking_and_flags_refund(self):
        provision_staff_roles()
        User = get_user_model()
        receptionist = User.objects.create_user(username="cancel_reception", password="V4#kR7!mQ2pZ", is_staff=True)
        receptionist.groups.add(Group.objects.get(name="Réceptionniste"))
        self.reservation.status = "confirmed"
        self.reservation.payment_status = "partially_paid"
        self.reservation.arrival = timezone.localdate() + timedelta(days=5)
        self.reservation.save(update_fields=["status", "payment_status", "arrival"])
        cancelled = cancel_staff_reservation(reference=self.reservation.reference, actor=receptionist)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(cancelled.payment_status, "refund_pending")
        self.assertTrue(BusinessAuditLog.objects.filter(action="reservation.annulee_equipe", object_reference=self.reservation.reference).exists())

    def test_refund_reconciliation_tracks_partial_refunds_and_prevents_overpayment(self):
        provision_staff_roles()
        User = get_user_model()
        manager = User.objects.create_superuser(username="refund_manager", email="refund@example.test", password="M4#vP8!qZ2sR")
        self.reservation.status = "confirmed"
        self.reservation.arrival = timezone.localdate() + timedelta(days=5)
        self.reservation.save(update_fields=["status", "arrival"])
        receive_manual_reservation_payment(
            reference=self.reservation.reference, amount=50000, method="transfer", external_reference="PAY-1", actor=manager,
        )
        cancel_staff_reservation(reference=self.reservation.reference, actor=manager)
        with self.assertRaisesMessage(ValidationError, "dépasse le remboursement restant"):
            record_refund(source_type="reservation", reference=self.reservation.reference, amount=60000,
                method="transfer", external_reference="REF-TOO-MUCH", notes="", actor=manager)
        record_refund(source_type="reservation", reference=self.reservation.reference, amount=20000,
            method="transfer", external_reference="REF-1", notes="Premier versement", actor=manager)
        with self.assertRaisesMessage(ValidationError, "déjà été enregistrée"):
            record_refund(source_type="reservation", reference=self.reservation.reference, amount=10000,
                method="transfer", external_reference="REF-1", notes="Doublon", actor=manager)
        record_refund(source_type="reservation", reference=self.reservation.reference, amount=30000,
            method="transfer", external_reference="REF-2", notes="Solde", actor=manager)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.payment_status, "refunded")
        self.assertEqual(RefundRecord.objects.filter(reservation=self.reservation).count(), 2)

    def test_payment_reconciliation_page_is_restricted_and_lists_refunds(self):
        self.reservation.payment_status = "refund_pending"
        self.reservation.status = "cancelled"
        self.reservation.save(update_fields=["payment_status", "status"])
        response = self.client.get(reverse("rapprochement_paiements"))
        self.assertEqual(response.status_code, 302)
        manager = get_user_model().objects.create_superuser(username="recon_manager", email="recon@example.test", password="M4#vP8!qZ2sR")
        self.client.force_login(manager)
        response = self.client.get(reverse("rapprochement_paiements"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.reservation.reference)
        self.assertContains(response, "ne déclenche pas un transfert d’argent")

    def test_staff_can_reconcile_fedapay_by_rereading_provider_data(self):
        self.reservation.fedapay_transaction_id = "519991"
        self.reservation.payment_status = "pending"
        self.reservation.save(update_fields=["fedapay_transaction_id", "payment_status"])
        manager = get_user_model().objects.create_superuser(
            username="fedapay_reconcile", email="fedapay-reconcile@example.test", password="A7#xR2!kM8vP",
        )
        self.client.force_login(manager)
        url = reverse("rapprocher_transaction_fedapay", kwargs={
            "source_type": "reservation", "reference": self.reservation.reference,
        })
        provider = {"id": "519991", "amount": self.reservation.total_price, "status": "approved",
            "custom_metadata": {"reservation_reference": self.reservation.reference}}
        with patch("apps.core.views.retrieve_transaction", return_value=provider):
            response = self.client.post(url)
        self.assertRedirects(response, reverse("rapprochement_paiements"), fetch_redirect_response=False)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.payment_status, "paid")
        self.assertEqual(self.reservation.status, "confirmed")
        self.assertTrue(Invoice.objects.filter(reservation=self.reservation).exists())
        self.assertTrue(BusinessAuditLog.objects.filter(action="paiement.rapproche", object_reference=self.reservation.reference).exists())

    @override_settings(FEDAPAY_ENABLED=True)
    def test_partially_paid_stay_cannot_start_full_amount_fedapay_checkout(self):
        self.reservation.payment_status = "partially_paid"
        self.reservation.save(update_fields=["payment_status"])
        response = self.client.post(reverse("initier_paiement", kwargs={"reference": self.reservation.reference}))
        self.assertRedirects(response, reverse("reservation_confirmee", kwargs={"reference": self.reservation.reference}), fetch_redirect_response=False)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.payment_status, "partially_paid")
        self.assertFalse(self.reservation.fedapay_transaction_id)

    def test_reception_transition_checks_guarantee_and_checkout_housekeeping(self):
        provision_staff_roles()
        User = get_user_model()
        receptionist = User.objects.create_user(username="checkin_reception", password="V4#kR7!mQ2pZ", is_staff=True)
        receptionist.groups.add(Group.objects.get(name="Réceptionniste"))
        self.reservation.arrival = timezone.localdate()
        self.reservation.departure = timezone.localdate() + timedelta(days=1)
        self.reservation.status = "confirmed"
        self.reservation.save(update_fields=["arrival", "departure", "status"])
        self.client.force_login(receptionist)
        action_url = reverse("action_reservation", kwargs={"reference": self.reservation.reference})

        response = self.client.post(action_url, {"action": "checked_in"})
        self.assertRedirects(response, reverse("tableau_equipe"), fetch_redirect_response=False)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, "confirmed")

        self.reservation.guarantee_status = "secured"
        self.reservation.save(update_fields=["guarantee_status"])
        self.client.post(action_url, {"action": "checked_in"})
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, "checked_in")

        self.client.post(action_url, {"action": "checked_out"})
        self.reservation.refresh_from_db()
        self.unit.refresh_from_db()
        self.assertEqual(self.reservation.status, "checked_out")
        self.assertEqual(self.unit.housekeeping_status, "needs_cleaning")

    def test_cancel_confirmed_paid_reservation_records_refund_due(self):
        User = get_user_model()
        customer = User.objects.create_user(username="cancel_guest", email="cancel@example.test", password="G6#zB9!wL4sN")
        self.reservation.customer = customer
        self.reservation.arrival = timezone.localdate() + timedelta(days=5)
        self.reservation.status = "confirmed"
        self.reservation.payment_status = "paid"
        self.reservation.save(update_fields=["customer", "arrival", "status", "payment_status"])
        self.client.force_login(customer)

        response = self.client.post(reverse("annuler_reservation", kwargs={"reference": self.reservation.reference}))

        self.assertRedirects(response, reverse("mon_compte"), fetch_redirect_response=False)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, "cancelled")
        self.assertEqual(self.reservation.payment_status, "refund_pending")
        self.assertTrue(BusinessAuditLog.objects.filter(
            action="reservation.annulee", object_reference=self.reservation.reference,
        ).exists())

    def test_seasonal_rate_weekdays_discount_and_stay_restrictions(self):
        rate = RoomRate.objects.create(
            room=self.room, label="Promotion du lundi", start_date=date(2026, 12, 1),
            end_date=date(2027, 1, 1), price_per_night=80000, minimum_nights=3,
            weekdays=[0], arrival_weekdays=[0], discount_percent=25,
        )
        self.assertEqual(quote_room_stay(self.room, date(2026, 12, 7), date(2026, 12, 10)), 156000)
        with self.assertRaisesMessage(ValidationError, "minimum de 3 nuit(s)"):
            quote_room_stay(self.room, date(2026, 12, 7), date(2026, 12, 9))
        with self.assertRaisesMessage(ValidationError, "n’autorise pas une arrivée"):
            quote_room_stay(self.room, date(2026, 12, 8), date(2026, 12, 11))
        self.assertEqual(rate.discount_percent, 25)

    def test_stay_fees_are_calculated_and_saved_as_a_snapshot(self):
        RoomFee.objects.create(name="Forfait nuit", amount_type="fixed", amount=1000, per_night=True)
        RoomFee.objects.create(name="Service", room=self.room, amount_type="percent", amount=10)
        quote = quote_room_stay(self.room, date(2026, 12, 12), date(2026, 12, 14))
        self.assertEqual(quote, 107600)

        reservation = create_reservation(form_data={
            "room": self.room, "arrival": date(2026, 12, 12), "departure": date(2026, 12, 14),
            "guests": 2, "guest_name": "Guest fees", "guest_email": "fees@example.test", "guest_phone": "",
        })
        self.assertEqual(reservation.total_price, 107600)
        self.assertEqual(reservation.fees_amount, 11600)

    def test_promotion_code_is_normalized_applied_and_usage_limited(self):
        today = timezone.localdate()
        promo = PromotionCode.objects.create(
            code=" automne10 ", label="Offre de démonstration", discount_type="percent",
            discount_value=10, starts_on=today, ends_on=today + timedelta(days=10), maximum_uses=1,
        )
        reservation = create_reservation(form_data={
            "room": self.room, "arrival": date(2026, 12, 12), "departure": date(2026, 12, 14),
            "guests": 2, "guest_name": "Guest promotion", "guest_email": "promo@example.test",
            "promo_code": " automne10 ",
        })
        self.assertEqual(promo.code, "AUTOMNE10")
        self.assertEqual(reservation.promotion_code_used, "AUTOMNE10")
        self.assertEqual(reservation.discount_amount, 9600)
        self.assertEqual(reservation.total_price, 86400)
        with self.assertRaisesMessage(ValidationError, "utilisations prévu"):
            create_reservation(form_data={
                "room": self.room, "arrival": date(2026, 12, 14), "departure": date(2026, 12, 16),
                "guests": 2, "guest_name": "Guest promotion 2", "guest_email": "promo2@example.test",
                "promo_code": "AUTOMNE10",
            })
        reservation.status = "cancelled"
        reservation.save(update_fields=["status"])
        renewed = create_reservation(form_data={
            "room": self.room, "arrival": date(2026, 12, 14), "departure": date(2026, 12, 16),
            "guests": 2, "guest_name": "Guest promotion 3", "guest_email": "promo3@example.test",
            "promo_code": "AUTOMNE10",
        })
        self.assertEqual(renewed.discount_amount, 9600)

    def test_percentage_room_fee_rejects_invalid_local_configuration(self):
        fee = RoomFee(name="Frais incorrect", amount_type="percent", amount=Decimal("110"))
        with self.assertRaisesMessage(ValidationError, "compris entre 0 et 100"):
            fee.full_clean()

    @override_settings(FEDAPAY_WEBHOOK_SECRET="test_endpoint_secret", FEDAPAY_ENABLED=True)
    def test_signed_fedapay_webhook_is_idempotent_and_rechecks_provider(self):
        self.reservation.fedapay_transaction_id = "123456"
        self.reservation.payment_status = "pending"
        self.reservation.save(update_fields=["fedapay_transaction_id", "payment_status"])
        event_payload = json.dumps({
            "id": "evt_123456", "type": "transaction.approved", "object": "transaction", "object_id": 123456,
            "entity": {"id": 123456, "amount": self.reservation.total_price, "status": "approved", "custom_metadata": {"reservation_reference": self.reservation.reference}},
        }, separators=(",", ":")).encode()
        timestamp = int(time.time())
        signature = hmac.new(b"test_endpoint_secret", str(timestamp).encode() + b"." + event_payload, hashlib.sha256).hexdigest()
        headers = {"HTTP_X_FEDAPAY_SIGNATURE": f"t={timestamp},s={signature}"}
        provider_data = {
            "id": 123456, "amount": self.reservation.total_price, "status": "approved",
            "custom_metadata": {"reservation_reference": self.reservation.reference},
        }
        with patch("apps.core.views.retrieve_transaction", return_value=provider_data):
            first = self.client.post(reverse("fedapay_webhook"), data=event_payload, content_type="application/json", **headers)
            duplicate = self.client.post(reverse("fedapay_webhook"), data=event_payload, content_type="application/json", **headers)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, "confirmed")
        self.assertEqual(self.reservation.payment_status, "paid")
        payment_logs = BusinessAuditLog.objects.filter(action="paiement.confirme", object_reference=self.reservation.reference)
        self.assertEqual(payment_logs.count(), 1)
        self.assertEqual(Invoice.objects.filter(reservation=self.reservation).count(), 1)
        self.assertEqual(FedaPayWebhookEvent.objects.get(event_id="evt_123456").status, "processed")

    def test_rate_admin_form_is_in_french_and_exposes_weekday_rules(self):
        User = get_user_model()
        administrator = User.objects.create_superuser(username="rate_admin", email="rate-admin@example.test", password="A7#uJ5!nK3rF")
        self.client.force_login(administrator)
        response = self.client.get(reverse("admin:core_roomrate_add"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jours tarifés")
        self.assertContains(response, "Jours d’arrivée autorisés")

    @override_settings(FEDAPAY_WEBHOOK_SECRET="test_endpoint_secret")
    def test_fedapay_webhook_rejects_invalid_signature(self):
        body = b'{"id":"evt_bad","type":"transaction.approved","object_id":123}'
        response = self.client.post(
            reverse("fedapay_webhook"), data=body, content_type="application/json",
            HTTP_X_FEDAPAY_SIGNATURE=f"t={int(time.time())},s=invalid",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(FedaPayWebhookEvent.objects.exists())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_verification_email_contains_link_and_one_time_code(self):
        User = get_user_model()
        user = User.objects.create_user(username="code_guest", email="code@example.test", password="S8#tP4!mK2xR", is_active=False)
        profile = CustomerProfile.objects.create(user=user)
        request = RequestFactory().get("/", HTTP_HOST="localhost:8091")

        _send_activation_email(request, user)

        message = mail.outbox[-1].body
        link = re.search(r"http://localhost:8091/compte/verification/[^\s]+", message)
        code = re.search(r"code[^\n]*: ([A-HJ-NP-Z2-9]{8})", message, re.IGNORECASE)
        profile.refresh_from_db()
        self.assertIsNotNone(link)
        self.assertIsNotNone(code)
        self.assertNotEqual(profile.email_verification_code_hash, code.group(1))
        self.assertGreater(profile.email_verification_code_expires_at, timezone.now())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_verification_code_activates_account_and_is_consumed(self):
        User = get_user_model()
        user = User.objects.create_user(username="code_guest", email="code@example.test", password="S8#tP4!mK2xR", is_active=False)
        profile = CustomerProfile.objects.create(user=user)
        _send_activation_email(RequestFactory().get("/", HTTP_HOST="localhost:8091"), user)
        code = re.search(r"code[^\n]*: ([A-HJ-NP-Z2-9]{8})", mail.outbox[-1].body, re.IGNORECASE).group(1)

        response = self.client.post(reverse("verification_envoyee"), {"email": user.email, "code": code})

        self.assertRedirects(response, reverse("mon_compte"), fetch_redirect_response=False)
        user.refresh_from_db()
        profile.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(profile.email_verified)
        self.assertEqual(profile.email_verification_code_hash, "")
        self.assertIsNone(profile.email_verification_code_expires_at)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_verification_link_activates_account(self):
        User = get_user_model()
        user = User.objects.create_user(username="link_guest", email="link@example.test", password="R7#uM3!pJ9xT", is_active=False)
        CustomerProfile.objects.create(user=user)
        _send_activation_email(RequestFactory().get("/", HTTP_HOST="localhost:8091"), user)
        link = re.search(r"http://localhost:8091/compte/verification/[^\s]+", mail.outbox[-1].body).group(0)

        response = self.client.get(urlsplit(link).path)

        self.assertRedirects(response, reverse("mon_compte"), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_expired_verification_code_does_not_activate_account(self):
        from django.contrib.auth.hashers import make_password

        User = get_user_model()
        user = User.objects.create_user(username="expired_guest", email="expired@example.test", password="T6#mR2!pK8xS", is_active=False)
        profile = CustomerProfile.objects.create(
            user=user,
            email_verification_code_hash=make_password("ABCD2345"),
            email_verification_code_expires_at=timezone.now() - timedelta(minutes=1),
        )

        response = self.client.post(reverse("verification_envoyee"), {"email": user.email, "code": "ABCD2345"})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].is_valid())
        user.refresh_from_db()
        profile.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(profile.email_verified)
