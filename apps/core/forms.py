from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.utils import timezone

from .models import HotelReview, MenuItem, MenuOption, PromotionCode, Reservation, RestaurantTable, Room, RoomUnit
from .services import get_available_room_units


class HotelSetupForm(forms.Form):
    hotel_name = forms.CharField(label="Nom de démonstration", max_length=120, initial="Naya Marina")
    city = forms.CharField(label="Ville", max_length=80, initial="Cotonou")
    address = forms.CharField(label="Adresse de démonstration", max_length=200, required=False)
    contact_email = forms.EmailField(label="Courriel de contact local", required=False)
    phone = forms.CharField(label="Téléphone de démonstration", max_length=40, required=False)
    developer_name = forms.CharField(label="Nom du développeur", max_length=120, required=False)
    demo_mode = forms.BooleanField(label="Charger les chambres et tarifs fictifs de démonstration", required=False, initial=True)


class HotelReviewForm(forms.ModelForm):
    class Meta:
        model = HotelReview
        fields = ("rating", "comment")
        labels = {"rating": "Votre note", "comment": "Votre avis"}
        widgets = {
            "rating": forms.Select(choices=[(n, f"{n}/5") for n in range(5, 0, -1)]),
            "comment": forms.Textarea(attrs={"rows": 5, "maxlength": 2000, "placeholder": "Racontez votre expérience…"}),
        }

    def clean_comment(self):
        comment = self.cleaned_data["comment"].strip()
        if len(comment) < 15:
            raise ValidationError("L’avis doit contenir au moins 15 caractères.")
        return comment


class NewsletterSignupForm(forms.Form):
    email = forms.EmailField(label="Votre adresse e-mail", max_length=254)

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class AdminSetupForm(forms.Form):
    username = forms.RegexField(label="Identifiant administrateur", regex=r"^[\w.@+-]+$", max_length=150)
    email = forms.EmailField(label="Adresse courriel administrateur")
    first_name = forms.CharField(label="Prénom", max_length=150)
    last_name = forms.CharField(label="Nom", max_length=150)
    password = forms.CharField(label="Mot de passe administrateur (12 caractères minimum)", widget=forms.PasswordInput, min_length=12)
    password_confirm = forms.CharField(label="Confirmer le mot de passe", widget=forms.PasswordInput)

    def clean(self):
        data = super().clean()
        if data.get("password") != data.get("password_confirm"):
            self.add_error("password_confirm", "Les mots de passe ne correspondent pas.")
        user_model = get_user_model()
        if data.get("username") and user_model.objects.filter(username__iexact=data["username"]).exists():
            self.add_error("username", "Cet identifiant existe déjà.")
        if data.get("email") and user_model.objects.filter(email__iexact=data["email"]).exists():
            self.add_error("email", "Cette adresse courriel est déjà utilisée.")
        if data.get("password") and data.get("password_confirm") == data.get("password"):
            candidate = user_model(username=data.get("username", ""), email=data.get("email", ""), first_name=data.get("first_name", ""), last_name=data.get("last_name", ""))
            try:
                validate_password(data["password"], candidate)
            except ValidationError as error:
                self.add_error("password", error)
        return data


STAFF_ROLE_CHOICES = [
    ("Gestionnaire hôtelier", "Gestionnaire hôtelier"),
    ("Réceptionniste", "Réceptionniste"),
    ("Équipe restaurant", "Équipe restaurant"),
]


class StaffRoleForm(forms.Form):
    role = forms.ChoiceField(label="Rôle", choices=STAFF_ROLE_CHOICES)


class RestaurantStockAdjustmentForm(forms.Form):
    delta = forms.IntegerField(label="Variation (+ entrée, − sortie)", min_value=-1000, max_value=1000)
    reason = forms.CharField(label="Motif obligatoire", max_length=200, strip=True)

    def clean_delta(self):
        delta = self.cleaned_data["delta"]
        if delta == 0:
            raise ValidationError("La variation doit être différente de zéro.")
        return delta

    def clean_reason(self):
        reason = self.cleaned_data["reason"]
        if not reason:
            raise ValidationError("Indique le motif de la correction de stock.")
        return reason


class RestaurantStockMinimumForm(forms.Form):
    minimum = forms.IntegerField(label="Seuil d’alerte", min_value=0, max_value=100000)


class StaffMemberForm(forms.Form):
    username = forms.RegexField(label="Identifiant", regex=r"^[\w.@+-]+$", max_length=150)
    email = forms.EmailField(label="Adresse e-mail")
    first_name = forms.CharField(label="Prénom", max_length=150)
    last_name = forms.CharField(label="Nom", max_length=150)
    role = forms.ChoiceField(label="Rôle", choices=STAFF_ROLE_CHOICES)
    password = forms.CharField(label="Mot de passe (12 caractères minimum)", min_length=12, widget=forms.PasswordInput)

    def clean_username(self):
        username = self.cleaned_data["username"]
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise ValidationError("Cet identifiant est déjà utilisé.")
        return username


class CustomerRegistrationForm(forms.Form):
    username = forms.RegexField(label="Identifiant", regex=r"^[\w.@+-]+$", max_length=150)
    first_name = forms.CharField(label="Prénom", max_length=150)
    last_name = forms.CharField(label="Nom", max_length=150)
    email = forms.EmailField(label="Adresse e-mail")
    phone = forms.CharField(label="Téléphone", max_length=40, required=False)
    password = forms.CharField(label="Mot de passe", widget=forms.PasswordInput, min_length=12)
    password_confirm = forms.CharField(label="Confirmer le mot de passe", widget=forms.PasswordInput)

    def clean(self):
        data = super().clean()
        user_model = get_user_model()
        if data.get("username") and user_model.objects.filter(username=data["username"]).exists():
            self.add_error("username", "Cet identifiant est déjà utilisé.")
        if data.get("email") and user_model.objects.filter(email__iexact=data["email"]).exists():
            self.add_error("email", "Cette adresse e-mail est déjà associée à un compte.")
        if data.get("password") != data.get("password_confirm"):
            self.add_error("password_confirm", "Les mots de passe ne correspondent pas.")
        elif data.get("password"):
            candidate = user_model(username=data.get("username", ""), email=data.get("email", ""), first_name=data.get("first_name", ""), last_name=data.get("last_name", ""))
            try:
                validate_password(data["password"], candidate)
            except ValidationError as error:
                self.add_error("password", error)
        return data


class CustomerLoginForm(AuthenticationForm):
    username = forms.CharField(label="Identifiant", widget=forms.TextInput(attrs={"autofocus": True}))
    password = forms.CharField(label="Mot de passe", strip=False, widget=forms.PasswordInput)


class EmailVerificationResendForm(forms.Form):
    email = forms.EmailField(label="Adresse e-mail")


class EmailVerificationCodeForm(forms.Form):
    email = forms.EmailField(label="Adresse e-mail")
    code = forms.RegexField(
        label="Code de vérification",
        regex=r"^[A-HJ-NP-Z2-9]{8}$",
        min_length=8,
        max_length=8,
        error_messages={"invalid": "Saisis le code de 8 lettres et chiffres reçu par e-mail."},
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "autocapitalize": "characters", "maxlength": 8}),
    )

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()


class RestaurantOrderForm(forms.Form):
    guest_name = forms.CharField(label="Nom complet", max_length=160)
    guest_email = forms.EmailField(label="Adresse e-mail")
    guest_phone = forms.CharField(label="Téléphone", max_length=40, required=False)
    service_type = forms.ChoiceField(label="Mode de service", choices=[("table", "À table"), ("room", "Service en chambre"), ("takeaway", "À emporter")])
    room_number = forms.CharField(label="Numéro de chambre", max_length=20, required=False)
    table = forms.ModelChoiceField(label="Table", queryset=RestaurantTable.objects.none(), required=False)
    notes = forms.CharField(label="Instructions particulières", widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(self, *args, **kwargs):
        self.menu_items = list(MenuItem.objects.filter(is_available=True))
        super().__init__(*args, **kwargs)
        self.fields["table"].queryset = RestaurantTable.objects.filter(is_active=True, status="available")
        for item in self.menu_items:
            self.fields[f"item_{item.pk}"] = forms.IntegerField(
                label=f"{item.name} — {item.price} FCFA",
                min_value=0,
                max_value=20,
                required=False,
                initial=0,
                widget=forms.NumberInput(attrs={"min": 0, "max": 20}),
            )
            options = list(item.options.filter(is_available=True))
            if options:
                self.fields[f"options_{item.pk}"] = forms.ModelMultipleChoiceField(
                    label=f"Options pour {item.name}", queryset=MenuOption.objects.filter(pk__in=[option.pk for option in options]),
                    required=False, widget=forms.CheckboxSelectMultiple,
                )

    def clean(self):
        data = super().clean()
        selected = [(item, data.get(f"item_{item.pk}") or 0) for item in self.menu_items]
        selected = [(item, quantity) for item, quantity in selected if quantity > 0]
        if not selected:
            raise forms.ValidationError("Choisis au moins un article dans la carte.")
        if data.get("service_type") == "room" and not data.get("room_number", "").strip():
            self.add_error("room_number", "Indique le numéro de ta chambre pour le service en chambre.")
        if data.get("service_type") == "table" and not data.get("table"):
            self.add_error("table", "Choisis une table disponible pour le service à table.")
        selected_options = {
            item.pk: list(data.get(f"options_{item.pk}") or []) for item, _ in selected
        }
        for item, options in selected_options.items():
            if any(option.item_id != item for option in options):
                raise forms.ValidationError("Une option choisie ne correspond pas à l’article commandé.")
        data["selected_items"] = selected
        data["selected_options"] = selected_options
        return data


class ReservationForm(forms.ModelForm):
    promo_code = forms.CharField(label="Code promotionnel (facultatif)", max_length=32, required=False)

    def __init__(self, *args, **kwargs):
        self.exclude_reservation_id = kwargs.pop("exclude_reservation_id", None)
        self.staff_mode = kwargs.pop("staff_mode", False)
        super().__init__(*args, **kwargs)
        self.fields["promo_code"].initial = getattr(self.instance, "promotion_code_used", "")
        self.fields["room"].queryset = Room.objects.filter(is_available=True)
        if self.staff_mode:
            self.fields["room_unit"] = forms.ModelChoiceField(
                label="Unité physique à affecter", queryset=RoomUnit.objects.none(), required=True,
            )
            self.fields["room_unit"].initial = getattr(self.instance, "room_unit", None)
            room_id = self.data.get("room") if self.is_bound else getattr(self.instance, "room_id", None)
            room = Room.objects.filter(pk=room_id, is_available=True).first() if room_id else None
            if room:
                try:
                    arrival = forms.DateField().clean(self.data.get("arrival")) if self.is_bound else self.instance.arrival
                    departure = forms.DateField().clean(self.data.get("departure")) if self.is_bound else self.instance.departure
                except ValidationError:
                    arrival = departure = None
                if arrival and departure and departure > arrival:
                    self.fields["room_unit"].queryset = get_available_room_units(
                        room, arrival, departure, exclude_reservation_id=self.exclude_reservation_id,
                    )
                else:
                    self.fields["room_unit"].queryset = RoomUnit.objects.filter(room=room, is_active=True)

    class Meta:
        model = Reservation
        fields = ["room", "guest_name", "guest_email", "guest_phone", "arrival", "departure", "guests"]
        labels = {
            "room": "Chambre souhaitée",
            "guest_name": "Nom complet",
            "guest_email": "Adresse e-mail",
            "guest_phone": "Téléphone",
            "arrival": "Date d’arrivée",
            "departure": "Date de départ",
            "guests": "Nombre de voyageurs",
        }
        widgets = {"arrival": forms.DateInput(attrs={"type": "date"}), "departure": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        data = super().clean()
        data["promo_code"] = (data.get("promo_code") or "").strip().upper()
        arrival, departure, room, guests = (data.get(k) for k in ("arrival", "departure", "room", "guests"))
        if arrival and arrival < timezone.localdate():
            self.add_error("arrival", "La date d’arrivée ne peut pas être passée.")
        if arrival and departure and departure <= arrival:
            self.add_error("departure", "Le départ doit être après l’arrivée.")
        if room and guests and guests > room.capacity:
            self.add_error("guests", f"Cette chambre accueille au maximum {room.capacity} personne(s).")
        if room and arrival and departure and departure > arrival:
            available = get_available_room_units(
                room, arrival, departure, exclude_reservation_id=self.exclude_reservation_id,
            )
            if not available.exists():
                self.add_error("room", "Aucune chambre physique de cette catégorie n’est disponible pour ces dates.")
            if self.staff_mode and data.get("room_unit") and not available.filter(pk=data["room_unit"].pk).exists():
                self.add_error("room_unit", "Cette unité n’est plus libre aux dates demandées.")
        return data


class RoomMoveForm(forms.Form):
    room_unit = forms.ModelChoiceField(label="Nouvelle chambre physique", queryset=RoomUnit.objects.none())

    def __init__(self, *args, reservation, **kwargs):
        super().__init__(*args, **kwargs)
        available = get_available_room_units(
            reservation.room, reservation.arrival, reservation.departure,
            exclude_reservation_id=reservation.pk,
        ).exclude(pk=reservation.room_unit_id)
        self.fields["room_unit"].queryset = available.filter(housekeeping_status__in=["clean", "inspected"])


class ManualReservationPaymentForm(forms.Form):
    amount = forms.IntegerField(label="Montant reçu (FCFA)", min_value=1)
    method = forms.ChoiceField(label="Mode de paiement", choices=[
        ("cash", "Espèces"), ("transfer", "Virement"),
        ("mobile_money", "Mobile Money encaissé manuellement"), ("card", "Carte au terminal"),
    ])
    reference = forms.CharField(label="Référence / numéro de reçu", max_length=100, required=False)

    def __init__(self, *args, max_amount, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount"].max_value = max_amount
        self.fields["amount"].validators.append(MaxValueValidator(max_amount))
        self.fields["amount"].initial = max_amount


class RefundReconciliationForm(forms.Form):
    amount = forms.IntegerField(label="Montant effectivement remboursé (FCFA)", min_value=1)
    method = forms.ChoiceField(label="Mode de remboursement", choices=[
        ("fedapay_dashboard", "FedaPay — tableau de bord"), ("transfer", "Virement bancaire"),
        ("cash", "Espèces"), ("mobile_money", "Mobile Money manuel"),
    ])
    reference = forms.CharField(label="Référence de remboursement / justificatif", max_length=120)
    notes = forms.CharField(label="Note", max_length=240, required=False)

    def __init__(self, *args, max_amount, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount"].max_value = max_amount
        self.fields["amount"].validators.append(MaxValueValidator(max_amount))
        self.fields["amount"].initial = max_amount


class AvailabilitySearchForm(forms.Form):
    arrival = forms.DateField(label="Date d’arrivée", widget=forms.DateInput(attrs={"type": "date"}))
    departure = forms.DateField(label="Date de départ", widget=forms.DateInput(attrs={"type": "date"}))
    guests = forms.IntegerField(label="Voyageurs", min_value=1, max_value=12, initial=2)
    category = forms.ChoiceField(label="Catégorie", required=False)
    min_price = forms.IntegerField(label="Budget minimum par nuit (FCFA)", required=False, min_value=0)
    max_price = forms.IntegerField(label="Budget maximum par nuit (FCFA)", required=False, min_value=0)
    sort_by = forms.ChoiceField(label="Trier par", required=False, choices=[("recommended", "Recommandées"), ("price_asc", "Prix croissant"), ("price_desc", "Prix décroissant"), ("capacity", "Capacité")])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        categories = Room.objects.filter(is_available=True).order_by("category").values_list("category", flat=True).distinct()
        self.fields["category"].choices = [("", "Toutes les catégories")] + [(category, category) for category in categories]

    def clean(self):
        data = super().clean()
        arrival, departure = data.get("arrival"), data.get("departure")
        if arrival and arrival < timezone.localdate():
            self.add_error("arrival", "La date d’arrivée ne peut pas être passée.")
        if arrival and departure and departure <= arrival:
            self.add_error("departure", "Le départ doit être après l’arrivée.")
        minimum, maximum = data.get("min_price"), data.get("max_price")
        if minimum is not None and maximum is not None and maximum < minimum:
            self.add_error("max_price", "Le budget maximum doit être supérieur ou égal au budget minimum.")
        return data
