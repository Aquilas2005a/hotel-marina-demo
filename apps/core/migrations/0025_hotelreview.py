import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0024_menuitem_stock_minimum_restaurantstockmovement"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="HotelReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("rating", models.PositiveSmallIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(5)])),
                ("comment", models.TextField(max_length=2000)),
                ("status", models.CharField(choices=[("pending", "À modérer"), ("published", "Publié"), ("rejected", "Refusé")], default="pending", max_length=12)),
                ("hotel_response", models.TextField(blank=True, max_length=2000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("moderated_at", models.DateTimeField(blank=True, null=True)),
                ("guest", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hotel_reviews", to=settings.AUTH_USER_MODEL)),
                ("reservation", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="hotel_review", to="core.reservation")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="hotelreview",
            index=models.Index(fields=["status", "-created_at"], name="core_hotelr_status_a3bb2d_idx"),
        ),
    ]
