import django.db.models.deletion
import apps.core.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0025_hotelreview"),
    ]

    operations = [
        migrations.CreateModel(
            name="NewsletterSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("status", models.CharField(choices=[("pending", "Confirmation en attente"), ("active", "Inscription confirmée"), ("unsubscribed", "Désinscrit")], default="pending", max_length=16)),
                ("token_version", models.CharField(default=apps.core.models.generate_newsletter_token, editable=False, max_length=64)),
                ("subscribed_at", models.DateTimeField(auto_now_add=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("unsubscribed_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-subscribed_at"]},
        ),
    ]
