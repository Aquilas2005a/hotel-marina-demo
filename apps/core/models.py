import secrets

from django.conf import settings
from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator


class HotelProfile(models.Model):
    hotel_name = models.CharField(max_length=120, default="Naya Marina")
    city = models.CharField(max_length=80, default="Cotonou")
    address = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    developer_name = models.CharField(max_length=120, blank=True)
    demo_mode = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.hotel_name


class Room(models.Model):
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    capacity = models.PositiveSmallIntegerField(default=2)
    units_total = models.PositiveSmallIntegerField(default=1, help_text="Nombre d’unités physiques actives (mis à jour depuis la liste des chambres)")
    price_per_night = models.PositiveIntegerField(help_text="Prix en FCFA")
    image_url = models.URLField(blank=True)
    is_available = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class RoomUnit(models.Model):
    HOUSEKEEPING_CHOICES = [
        ("clean", "Propre et prête"),
        ("needs_cleaning", "À nettoyer"),
        ("cleaning", "Nettoyage en cours"),
        ("inspected", "Contrôlée"),
    ]

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="units")
    code = models.CharField(max_length=40, help_text="Identifiant interne : numéro de chambre ou code d’unité")
    is_active = models.BooleanField(default=True)
    housekeeping_status = models.CharField(max_length=20, choices=HOUSEKEEPING_CHOICES, default="clean", verbose_name="État du ménage")
    notes = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["room__name", "code"]
        constraints = [models.UniqueConstraint(fields=["room", "code"], name="unique_room_unit_code")]

    def __str__(self):
        return f"{self.room.name} — {self.code}"


class RoomBlock(models.Model):
    BLOCK_TYPES = [("maintenance", "Maintenance"), ("out_of_service", "Hors service")]

    unit = models.ForeignKey(RoomUnit, on_delete=models.CASCADE, related_name="blocks")
    kind = models.CharField(max_length=20, choices=BLOCK_TYPES, default="maintenance")
    start_date = models.DateField(help_text="Premier jour bloqué")
    end_date = models.DateField(help_text="Premier jour de retour à la disponibilité")
    note = models.CharField(max_length=240, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_date", "unit__code"]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "La fin du blocage doit être après son début."})

    def __str__(self):
        return f"{self.unit} — {self.get_kind_display()} ({self.start_date} au {self.end_date})"


class RoomRate(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="seasonal_rates")
    label = models.CharField(max_length=100, help_text="Ex. Haute saison, fêtes, week-end")
    start_date = models.DateField(help_text="Premier jour où ce tarif s’applique")
    end_date = models.DateField(help_text="Premier jour où ce tarif ne s’applique plus")
    price_per_night = models.PositiveIntegerField(help_text="Prix par nuit en FCFA")
    minimum_nights = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    maximum_nights = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    weekdays = models.JSONField(default=list, blank=True, help_text="Jours applicables : 0=lundi à 6=dimanche ; vide=tous les jours")
    arrival_weekdays = models.JSONField(default=list, blank=True, help_text="Jours d’arrivée autorisés dans cette période ; vide=tous les jours")
    departure_weekdays = models.JSONField(default=list, blank=True, help_text="Jours de départ autorisés dans cette période ; vide=tous les jours")
    discount_percent = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(90)], help_text="Réduction du tarif en pourcentage, de 0 à 90")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["start_date", "room__name"]
        indexes = [models.Index(fields=["room", "is_active", "start_date", "end_date"])]

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            errors["end_date"] = "La fin du tarif doit être après son début."
        if self.maximum_nights and self.maximum_nights < self.minimum_nights:
            errors["maximum_nights"] = "La durée maximale doit être supérieure ou égale à la durée minimale."
        for field_name in ("weekdays", "arrival_weekdays", "departure_weekdays"):
            values = getattr(self, field_name)
            if not isinstance(values, list) or any(day not in range(7) for day in values):
                errors[field_name] = "Sélectionne des jours valides de 0 (lundi) à 6 (dimanche)."
        if self.room_id and self.start_date and self.end_date:
            overlaps = RoomRate.objects.filter(
                room_id=self.room_id,
                is_active=True,
                start_date__lt=self.end_date,
                end_date__gt=self.start_date,
            ).exclude(pk=self.pk)
            if self.is_active and overlaps.exists():
                errors["start_date"] = "Cette période chevauche déjà un autre tarif actif pour cette chambre."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.room.name} — {self.label} ({self.start_date} au {self.end_date})"


class RoomFee(models.Model):
    AMOUNT_TYPES = [("fixed", "Montant fixe en FCFA"), ("percent", "Pourcentage du séjour")]

    name = models.CharField(max_length=100)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, null=True, blank=True, related_name="fees", help_text="Vide : frais appliqués à toutes les catégories")
    amount_type = models.CharField(max_length=10, choices=AMOUNT_TYPES, default="fixed")
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    per_night = models.BooleanField(default=False, help_text="Pour un montant fixe, multiplie par le nombre de nuits")
    is_active = models.BooleanField(default=True)

    def clean(self):
        errors = {}
        if self.amount_type == "percent" and self.amount > 100:
            errors["amount"] = "Un frais en pourcentage doit être compris entre 0 et 100 %."
        if self.amount_type == "percent" and self.per_night:
            errors["per_night"] = "Un pourcentage s’applique au séjour et ne se multiplie pas par nuit."
        if errors:
            from django.core.exceptions import ValidationError
            raise ValidationError(errors)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PromotionCode(models.Model):
    DISCOUNT_CHOICES = [("percent", "Pourcentage"), ("fixed", "Montant fixe en FCFA")]

    code = models.CharField(max_length=32, unique=True)
    label = models.CharField(max_length=100)
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_CHOICES, default="percent")
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(1)])
    starts_on = models.DateField()
    ends_on = models.DateField(help_text="Dernier jour d’utilisation inclus")
    maximum_uses = models.PositiveIntegerField(null=True, blank=True, help_text="Laisser vide pour un nombre illimité")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-starts_on", "code"]

    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}
        self.code = (self.code or "").strip().upper()
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            errors["ends_on"] = "La date de fin doit être égale ou postérieure à la date de début."
        if self.discount_type == "percent" and self.discount_value > 90:
            errors["discount_value"] = "La réduction en pourcentage ne peut pas dépasser 90 %."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} — {self.label}"


class HotelInterior(models.Model):
    title = models.CharField(max_length=120)
    image_url = models.URLField()
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.title


class HotelAmenity(models.Model):
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=240, blank=True)
    icon = models.CharField(max_length=8, default="✦")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SocialLink(models.Model):
    PLATFORM_CHOICES = [
        ("facebook", "Facebook"), ("instagram", "Instagram"), ("youtube", "YouTube"),
        ("tiktok", "TikTok"), ("linkedin", "LinkedIn"), ("whatsapp", "WhatsApp"), ("other", "Autre"),
    ]

    platform = models.CharField(max_length=16, choices=PLATFORM_CHOICES)
    label = models.CharField(max_length=60, blank=True)
    url = models.URLField(max_length=500)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "platform", "id"]

    def __str__(self):
        return self.label or self.get_platform_display()


class SiteNotice(models.Model):
    KIND_CHOICES = [("info", "Information"), ("success", "Succès"), ("warning", "Avertissement")]

    title = models.CharField(max_length=120)
    message = models.CharField(max_length=500)
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default="info")
    link_label = models.CharField(max_length=60, blank=True)
    link_url = models.URLField(max_length=500, blank=True)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True, help_text="Laisser vide pour une notification sans date de fin")
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "starts_on", "id"]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.ends_on and self.starts_on and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "La fin doit être égale ou postérieure au début."})
        if bool(self.link_label) != bool(self.link_url):
            raise ValidationError("Le texte du lien et son adresse doivent être renseignés ensemble.")

    def __str__(self):
        return self.title


class NavigationLink(models.Model):
    label = models.CharField(max_length=60)
    url = models.CharField(max_length=500, help_text="Chemin local (/restaurant/, #chambres) ou adresse externe HTTPS")
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def clean(self):
        from django.core.exceptions import ValidationError
        from django.core.validators import URLValidator
        value = (self.url or "").strip()
        if value.startswith("//"):
            raise ValidationError({"url": "Les URL externes doivent utiliser HTTPS."})
        if value.startswith(("/", "#")):
            return
        try:
            URLValidator(schemes=["https"])(value)
        except ValidationError as error:
            raise ValidationError({"url": "Saisis un chemin local ou une adresse HTTPS."}) from error

    def save(self, *args, **kwargs):
        self.label = self.label.strip()
        self.url = self.url.strip()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.label


class Testimonial(models.Model):
    guest_name = models.CharField(max_length=120)
    quote = models.TextField()
    source_label = models.CharField(max_length=80, default="Avis de démonstration")
    rating = models.PositiveSmallIntegerField(default=5)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.guest_name


class HotelReview(models.Model):
    """Avis vérifiés liés à un séjour terminé, publiés après modération."""
    STATUS_CHOICES = [
        ("pending", "À modérer"),
        ("published", "Publié"),
        ("rejected", "Refusé"),
    ]

    reservation = models.OneToOneField("Reservation", on_delete=models.PROTECT, related_name="hotel_review")
    guest = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="hotel_reviews")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(max_length=2000)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="pending")
    hotel_response = models.TextField(max_length=2000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    moderated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"], name="core_hotelr_status_a3bb2d_idx")]

    def __str__(self):
        return f"{self.reservation.reference} · {self.rating}/5 · {self.get_status_display()}"


def generate_newsletter_token():
    return secrets.token_urlsafe(24)


class NewsletterSubscription(models.Model):
    STATUS_CHOICES = [
        ("pending", "Confirmation en attente"),
        ("active", "Inscription confirmée"),
        ("unsubscribed", "Désinscrit"),
    ]

    email = models.EmailField(unique=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    token_version = models.CharField(max_length=64, default=generate_newsletter_token, editable=False)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-subscribed_at"]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.email} · {self.get_status_display()}"


class CustomerProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer_profile")
    phone = models.CharField(max_length=40, blank=True)
    email_verified = models.BooleanField(default=False)
    email_verification_code_hash = models.CharField(max_length=256, blank=True)
    email_verification_code_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.get_full_name() or self.user.get_username()


class Reservation(models.Model):
    STATUS_CHOICES = [
        ("pending", "En attente de confirmation"),
        ("confirmed", "Confirmée"),
        ("checked_in", "Arrivée enregistrée"),
        ("checked_out", "Départ enregistré"),
        ("cancelled", "Annulée"),
    ]

    reference = models.CharField(max_length=12, unique=True)
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="reservations")
    room_unit = models.ForeignKey(RoomUnit, on_delete=models.PROTECT, null=True, blank=True, related_name="reservations", verbose_name="Chambre physique affectée")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="hotel_reservations")
    guest_name = models.CharField(max_length=160)
    guest_email = models.EmailField()
    guest_phone = models.CharField(max_length=40, blank=True)
    arrival = models.DateField()
    departure = models.DateField()
    guests = models.PositiveSmallIntegerField(default=1)
    total_price = models.PositiveIntegerField(help_text="Montant indicatif en FCFA")
    fees_amount = models.PositiveIntegerField(default=0, help_text="Frais de séjour figés au moment de la demande, en FCFA")
    promotion = models.ForeignKey(PromotionCode, on_delete=models.PROTECT, null=True, blank=True, related_name="reservations")
    promotion_code_used = models.CharField(max_length=32, blank=True, help_text="Code appliqué, conservé sur le dossier")
    discount_amount = models.PositiveIntegerField(default=0, help_text="Réduction figée lors de la réservation, en FCFA")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    PAYMENT_STATUS_CHOICES = [
        ("not_configured", "Paiement non configuré"),
        ("pending", "Paiement en attente"),
        ("paid", "Payée"),
        ("partially_paid", "Partiellement payée"),
        ("failed", "Échouée ou annulée"),
        ("refund_pending", "Remboursement à traiter"),
        ("refunded", "Remboursée"),
    ]
    payment_status = models.CharField(max_length=16, choices=PAYMENT_STATUS_CHOICES, default="not_configured")
    GUARANTEE_CHOICES = [
        ("pending", "Garantie à vérifier"),
        ("secured", "Garantie vérifiée"),
        ("not_required", "Garantie non exigée"),
    ]
    guarantee_status = models.CharField(max_length=16, choices=GUARANTEE_CHOICES, default="pending")
    fedapay_transaction_id = models.CharField(max_length=80, blank=True)
    payment_url = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference} — {self.guest_name}"

    @property
    def nights(self):
        return (self.departure - self.arrival).days

    @property
    def manual_amount_paid(self):
        return sum(payment.amount for payment in self.manual_payments.all())


class ReservationPaymentRecord(models.Model):
    METHOD_CHOICES = [
        ("cash", "Espèces"), ("transfer", "Virement"),
        ("mobile_money", "Mobile Money manuel"), ("card", "Carte au terminal"),
    ]
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, related_name="manual_payments")
    amount = models.PositiveIntegerField(help_text="Montant reçu en FCFA")
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    reference = models.CharField(max_length=100, blank=True, help_text="Référence bancaire ou ticket, si disponible")
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="recorded_reservation_payments")
    received_by_label = models.CharField(max_length=160, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["received_at", "pk"]

    def __str__(self):
        return f"{self.reservation.reference} · {self.amount} FCFA · {self.get_method_display()}"


class MenuItem(models.Model):
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=80, default="Carte")
    description = models.TextField(blank=True)
    price = models.PositiveIntegerField(help_text="Prix en FCFA")
    image_url = models.URLField(blank=True)
    is_available = models.BooleanField(default=True)
    track_stock = models.BooleanField(default=False, help_text="Décompte automatiquement le stock à la commande")
    stock_quantity = models.PositiveIntegerField(default=0)
    stock_minimum = models.PositiveIntegerField(default=0, help_text="Seuil d’alerte pour le réapprovisionnement")

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return self.name


class RestaurantStockMovement(models.Model):
    item = models.ForeignKey(MenuItem, on_delete=models.PROTECT, related_name="stock_movements")
    order = models.ForeignKey("RestaurantOrder", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_movements")
    delta = models.IntegerField(help_text="Positif pour une entrée, négatif pour une sortie")
    reason = models.CharField(max_length=200)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="restaurant_stock_movements")
    actor_label = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["item", "created_at"])]

    def __str__(self):
        return f"{self.item.name} · {self.delta:+d} · {self.created_at:%Y-%m-%d %H:%M}"


class RestaurantOrder(models.Model):
    SERVICE_CHOICES = [("table", "À table"), ("room", "Service en chambre"), ("takeaway", "À emporter")]
    STATUS_CHOICES = [("pending", "Reçue"), ("preparing", "En préparation"), ("ready", "Prête"), ("delivered", "Servie"), ("cancelled", "Annulée")]

    reference = models.CharField(max_length=12, unique=True)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="restaurant_orders")
    guest_name = models.CharField(max_length=160)
    guest_email = models.EmailField()
    guest_phone = models.CharField(max_length=40, blank=True)
    service_type = models.CharField(max_length=16, choices=SERVICE_CHOICES, default="table")
    room_number = models.CharField(max_length=20, blank=True)
    table = models.ForeignKey("RestaurantTable", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    notes = models.TextField(blank=True)
    total_price = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    PAYMENT_STATUS_CHOICES = [("not_configured", "Paiement non configuré"), ("pending", "Paiement en attente"), ("paid", "Payée"), ("failed", "Échouée ou annulée"), ("refund_pending", "Remboursement à traiter"), ("refunded", "Remboursée")]
    payment_status = models.CharField(max_length=16, choices=PAYMENT_STATUS_CHOICES, default="not_configured")
    fedapay_transaction_id = models.CharField(max_length=80, blank=True)
    payment_url = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference} — {self.guest_name}"


class RestaurantOrderLine(models.Model):
    order = models.ForeignKey(RestaurantOrder, on_delete=models.CASCADE, related_name="lines")
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT, related_name="order_lines")
    item_name = models.CharField(max_length=120)
    unit_price = models.PositiveIntegerField()
    quantity = models.PositiveSmallIntegerField()
    options = models.CharField(max_length=300, blank=True, help_text="Options choisies au moment de la commande")

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    def __str__(self):
        return f"{self.quantity} × {self.item_name}"


class MenuOption(models.Model):
    item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name="options")
    name = models.CharField(max_length=80)
    additional_price = models.PositiveIntegerField(default=0, help_text="Supplément en FCFA")
    is_available = models.BooleanField(default=True)

    class Meta:
        ordering = ["item__name", "name"]
        unique_together = [("item", "name")]

    def __str__(self):
        return f"{self.item.name} — {self.name}"


class RestaurantTable(models.Model):
    STATUS_CHOICES = [("available", "Disponible"), ("occupied", "Occupée"), ("closed", "Hors service")]
    name = models.CharField(max_length=40, unique=True)
    capacity = models.PositiveSmallIntegerField(default=2)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="available")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class Invoice(models.Model):
    number = models.CharField(max_length=40, unique=True)
    reservation = models.OneToOneField(Reservation, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice")
    restaurant_order = models.OneToOneField(RestaurantOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice")
    customer_name = models.CharField(max_length=160)
    customer_email = models.EmailField()
    description = models.CharField(max_length=240)
    subtotal = models.PositiveIntegerField(default=0, help_text="Montant hors taxe en FCFA")
    fees_amount = models.PositiveIntegerField(default=0, help_text="Frais de séjour inclus dans le sous-total")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Taux inclus au moment de l’émission")
    tax_amount = models.PositiveIntegerField(default=0, help_text="Taxe incluse en FCFA")
    amount = models.PositiveIntegerField(help_text="Montant facturé en FCFA")
    currency = models.CharField(max_length=3, default="XOF")
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at"]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(reservation__isnull=False, restaurant_order__isnull=True) | models.Q(reservation__isnull=True, restaurant_order__isnull=False)),
                name="invoice_exactly_one_source",
            )
        ]

    def __str__(self):
        return self.number


class RefundRecord(models.Model):
    METHOD_CHOICES = [
        ("fedapay_dashboard", "FedaPay — tableau de bord"),
        ("transfer", "Virement bancaire"), ("cash", "Espèces"),
        ("mobile_money", "Mobile Money manuel"),
    ]
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, null=True, blank=True, related_name="refunds")
    restaurant_order = models.ForeignKey(RestaurantOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="refunds")
    amount = models.PositiveIntegerField(help_text="Montant effectivement remboursé en FCFA")
    method = models.CharField(max_length=24, choices=METHOD_CHOICES)
    reference = models.CharField(max_length=120, help_text="Référence du remboursement ou du justificatif")
    notes = models.CharField(max_length=240, blank=True)
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="processed_hotel_refunds")
    processed_by_label = models.CharField(max_length=160, blank=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-processed_at", "-pk"]
        constraints = [models.CheckConstraint(
            condition=(models.Q(reservation__isnull=False, restaurant_order__isnull=True) | models.Q(reservation__isnull=True, restaurant_order__isnull=False)),
            name="refund_exactly_one_source",
        )]

    def __str__(self):
        source = self.reservation or self.restaurant_order
        return f"{source.reference if source else 'Remboursement'} · {self.amount} FCFA"


class FedaPayWebhookEvent(models.Model):
    STATUS_CHOICES = [
        ("received", "Reçu"),
        ("processed", "Traité"),
        ("ignored", "Ignoré"),
        ("failed", "À réessayer"),
    ]

    event_id = models.CharField(max_length=120, unique=True)
    event_type = models.CharField(max_length=100)
    transaction_id = models.CharField(max_length=80, blank=True)
    payload_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="received")
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=240, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.event_type} · {self.event_id} · {self.get_status_display()}"


class EmailOutbox(models.Model):
    STATUS_CHOICES = [
        ("queued", "En attente de nouvel essai"),
        ("sending", "En cours d’envoi"),
        ("sent", "Envoyé"),
        ("failed", "Échec définitif"),
    ]

    recipient = models.EmailField()
    subject = models.CharField(max_length=240)
    encrypted_content = models.TextField(blank=True, help_text="Contenu chiffré avec la clé d’outbox configurée côté serveur")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="queued")
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=120, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self):
        return f"{self.subject} · {self.recipient} · {self.get_status_display()}"


class BusinessAuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="business_audit_events")
    actor_label = models.CharField(max_length=160, blank=True, help_text="Nom conservé même si le compte est ensuite supprimé")
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_reference = models.CharField(max_length=120, blank=True)
    summary = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["created_at", "action"])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} · {self.action} · {self.object_reference}"
