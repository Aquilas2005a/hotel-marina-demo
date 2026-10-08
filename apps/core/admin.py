from django import forms
from django.contrib import admin
from django.utils import timezone
from .audit import record_business_event
from .models import BusinessAuditLog, CustomerProfile, EmailOutbox, HotelAmenity, HotelInterior, HotelProfile, HotelReview, Invoice, MenuItem, MenuOption, NavigationLink, NewsletterSubscription, PromotionCode, RefundRecord, Reservation, RestaurantOrder, RestaurantOrderLine, RestaurantStockMovement, RestaurantTable, Room, RoomBlock, RoomFee, RoomRate, RoomUnit, SiteNotice, SocialLink, Testimonial

admin.site.site_header = "Administration — Naya Marina"
admin.site.site_title = "Gestion hôtelière"
admin.site.index_title = "Réservations, chambres et restaurant"

class BusinessAuditAdminMixin:
    """Journalise les ajouts, changements et suppressions faits dans Django Admin."""

    def audit_object_type(self, obj):
        labels = {
            "Room": "Catégorie de chambre", "RoomUnit": "Unité de chambre", "RoomBlock": "Blocage de chambre",
            "RoomRate": "Tarif saisonnier", "RoomFee": "Frais de séjour", "PromotionCode": "Code promotionnel",
            "Reservation": "Réservation", "RestaurantOrder": "Commande restaurant", "MenuItem": "Article restaurant",
            "MenuOption": "Option de menu", "RestaurantTable": "Table restaurant", "HotelProfile": "Profil hôtel",
            "HotelInterior": "Contenu hôtel", "HotelAmenity": "Équipement hôtel", "Testimonial": "Témoignage", "HotelReview": "Avis client", "NewsletterSubscription": "Inscription newsletter", "SocialLink": "Lien social", "SiteNotice": "Notification du site", "NavigationLink": "Lien de navigation",
        }
        return labels.get(obj._meta.object_name, obj._meta.verbose_name)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        changed_fields = [field for field in form.changed_data if field not in {"password", "email_verification_code_hash"}]
        action = "admin.objet_modifie" if change else "admin.objet_ajoute"
        summary = f"Champs modifiés : {', '.join(changed_fields)}" if change else "Nouvel enregistrement créé."
        record_business_event(
            actor=request.user, action=action, object_type=self.audit_object_type(obj),
            reference=getattr(obj, "reference", None) or getattr(obj, "code", None) or obj.pk,
            summary=summary,
        )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        changed_sets = [formset.model._meta.verbose_name_plural for formset in formsets if formset.has_changed()]
        if changed_sets:
            record_business_event(
                actor=request.user, action="admin.relations_modifiees", object_type=self.audit_object_type(form.instance),
                reference=getattr(form.instance, "reference", None) or getattr(form.instance, "code", None) or form.instance.pk,
                summary=f"Formulaires associés modifiés : {', '.join(changed_sets)}",
            )

    def delete_model(self, request, obj):
        record_business_event(
            actor=request.user, action="admin.objet_supprime", object_type=self.audit_object_type(obj),
            reference=getattr(obj, "reference", None) or getattr(obj, "code", None) or obj.pk,
            summary="Enregistrement supprimé depuis l’administration.",
        )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            record_business_event(
                actor=request.user, action="admin.objet_supprime", object_type=self.audit_object_type(obj),
                reference=getattr(obj, "reference", None) or getattr(obj, "code", None) or obj.pk,
                summary="Enregistrement supprimé en lot depuis l’administration.",
            )
        super().delete_queryset(request, queryset)


@admin.register(HotelProfile)
class HotelProfileAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    pass
class RoomUnitInline(admin.TabularInline):
    model = RoomUnit
    extra = 0
    min_num = 1
    validate_min = True


class RoomBlockInline(admin.TabularInline):
    model = RoomBlock
    extra = 0


@admin.register(Room)
class RoomAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("name", "category", "capacity", "units_total", "price_per_night", "is_available")
    list_filter = ("category", "is_available")
    search_fields = ("name", "category")
    readonly_fields = ("units_total",)
    inlines = [RoomUnitInline]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.units_total = form.instance.units.filter(is_active=True).count()
        form.instance.save(update_fields=["units_total"])


@admin.register(RoomUnit)
class RoomUnitAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("code", "room", "housekeeping_status", "is_active")
    list_filter = ("housekeeping_status", "is_active", "room")
    search_fields = ("code", "room__name")
    inlines = [RoomBlockInline]


@admin.register(RoomBlock)
class RoomBlockAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("unit", "kind", "start_date", "end_date", "is_active")
    list_filter = ("kind", "is_active", "start_date")
    search_fields = ("unit__code", "unit__room__name", "note")


@admin.register(RoomRate)
class RoomRateAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("label", "room", "start_date", "end_date", "price_per_night", "minimum_nights", "maximum_nights", "discount_percent", "is_active")
    list_filter = ("is_active", "room")
    search_fields = ("label", "room__name")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        for field_name in ("weekdays", "arrival_weekdays", "departure_weekdays"):
            form.base_fields[field_name] = forms.TypedMultipleChoiceField(
                label={"weekdays": "Jours tarifés", "arrival_weekdays": "Jours d’arrivée autorisés", "departure_weekdays": "Jours de départ autorisés"}[field_name],
                choices=[(index, day) for index, day in enumerate(("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"))],
                coerce=int, required=False, widget=forms.CheckboxSelectMultiple,
            )
        return form

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        return initial | {"weekdays": [], "arrival_weekdays": [], "departure_weekdays": []}


@admin.register(RoomFee)
class RoomFeeAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("name", "room", "amount_type", "amount", "per_night", "is_active")
    list_filter = ("is_active", "amount_type", "per_night")
    search_fields = ("name", "room__name")


@admin.register(PromotionCode)
class PromotionCodeAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("code", "label", "discount_type", "discount_value", "starts_on", "ends_on", "maximum_uses", "is_active")
    list_filter = ("is_active", "discount_type", "starts_on", "ends_on")
    search_fields = ("code", "label")
admin.site.register(CustomerProfile)
@admin.register(MenuItem)
class MenuItemAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("name", "category", "price", "is_available", "track_stock", "stock_quantity", "stock_minimum")
    list_filter = ("category", "is_available", "track_stock")
    search_fields = ("name", "description")
    readonly_fields = ("stock_quantity",)


@admin.register(RestaurantStockMovement)
class RestaurantStockMovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "item", "delta", "reason", "order", "actor_label")
    list_filter = ("created_at",)
    search_fields = ("item__name", "reason", "order__reference", "actor_label")
    readonly_fields = tuple(field.name for field in RestaurantStockMovement._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MenuOption)
class MenuOptionAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("name", "item", "additional_price", "is_available")
    list_filter = ("is_available", "item__category")
    search_fields = ("name", "item__name")


@admin.register(RestaurantTable)
class RestaurantTableAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("name", "capacity", "status", "is_active")
    list_filter = ("status", "is_active")
    search_fields = ("name",)
@admin.register(HotelInterior)
class HotelInteriorAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    pass


@admin.register(HotelAmenity)
class HotelAmenityAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    pass


@admin.register(Testimonial)
class TestimonialAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    pass


@admin.register(HotelReview)
class HotelReviewAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("created_at", "reservation", "guest", "rating", "status", "moderated_at")
    list_filter = ("status", "rating", "created_at")
    search_fields = ("reservation__reference", "reservation__guest_name", "guest__email", "comment")
    readonly_fields = ("reservation", "guest", "rating", "comment", "created_at", "moderated_at")
    fields = ("reservation", "guest", "rating", "comment", "status", "hotel_response", "created_at", "moderated_at")

    def save_model(self, request, obj, form, change):
        if "status" in form.changed_data and obj.status != "pending":
            obj.moderated_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(NewsletterSubscription)
class NewsletterSubscriptionAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("email", "status", "subscribed_at", "confirmed_at", "unsubscribed_at")
    list_filter = ("status", "subscribed_at", "confirmed_at")
    search_fields = ("email",)
    readonly_fields = ("email", "token_version", "subscribed_at", "confirmed_at", "unsubscribed_at", "updated_at")
    fields = ("email", "status", "subscribed_at", "confirmed_at", "unsubscribed_at", "updated_at")


@admin.register(SocialLink)
class SocialLinkAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("platform", "label", "url", "sort_order", "is_active")
    list_filter = ("platform", "is_active")
    search_fields = ("label", "url")


@admin.register(SiteNotice)
class SiteNoticeAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("title", "kind", "starts_on", "ends_on", "sort_order", "is_active")
    list_filter = ("kind", "is_active", "starts_on", "ends_on")
    search_fields = ("title", "message", "link_label")


@admin.register(NavigationLink)
class NavigationLinkAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("label", "url", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("label", "url")


@admin.register(EmailOutbox)
class EmailOutboxAdmin(admin.ModelAdmin):
    list_display = ("created_at", "recipient", "subject", "status", "attempts", "sent_at", "last_error")
    list_filter = ("status", "created_at")
    search_fields = ("recipient", "subject")
    readonly_fields = ("recipient", "subject", "encrypted_content", "status", "attempts", "last_error", "next_attempt_at", "created_at", "updated_at", "sent_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BusinessAuditLog)
class BusinessAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_label", "action", "object_type", "object_reference", "summary")
    list_filter = ("action", "object_type", "created_at")
    search_fields = ("actor_label", "action", "object_type", "object_reference", "summary")
    readonly_fields = ("actor", "actor_label", "action", "object_type", "object_reference", "summary", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "customer_name", "subtotal", "fees_amount", "tax_amount", "amount", "currency", "issued_at")
    search_fields = ("number", "customer_name", "customer_email")
    readonly_fields = ("number", "reservation", "restaurant_order", "customer_name", "customer_email", "description", "subtotal", "fees_amount", "tax_rate", "tax_amount", "amount", "currency", "issued_at")


@admin.register(RefundRecord)
class RefundRecordAdmin(admin.ModelAdmin):
    list_display = ("processed_at", "reservation", "restaurant_order", "amount", "method", "reference", "processed_by_label")
    list_filter = ("method", "processed_at")
    search_fields = ("reference", "reservation__reference", "restaurant_order__reference", "processed_by_label")
    readonly_fields = tuple(field.name for field in RefundRecord._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Reservation)
class ReservationAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("reference", "guest_name", "room", "room_unit", "customer", "arrival", "departure", "status", "payment_status", "guarantee_status")
    list_filter = ("status", "payment_status", "arrival")
    search_fields = ("reference", "guest_name", "guest_email")
    readonly_fields = ("reference", "room_unit", "total_price", "fees_amount", "promotion", "promotion_code_used", "discount_amount", "created_at", "status", "payment_status")


class RestaurantOrderLineInline(admin.TabularInline):
    model = RestaurantOrderLine
    extra = 0
    readonly_fields = ("item_name", "unit_price", "quantity", "options")


@admin.register(RestaurantOrder)
class RestaurantOrderAdmin(BusinessAuditAdminMixin, admin.ModelAdmin):
    list_display = ("reference", "guest_name", "service_type", "table", "total_price", "status", "payment_status", "created_at")
    list_filter = ("status", "payment_status", "service_type", "created_at")
    search_fields = ("reference", "guest_name", "guest_email", "room_number")
    readonly_fields = ("reference", "total_price", "status", "payment_status", "created_at")
    inlines = [RestaurantOrderLineInline]
