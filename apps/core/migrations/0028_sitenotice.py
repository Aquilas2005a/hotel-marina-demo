from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0027_sociallink"),
    ]

    operations = [
        migrations.CreateModel(
            name="SiteNotice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=120)),
                ("message", models.CharField(max_length=500)),
                ("kind", models.CharField(choices=[("info", "Information"), ("success", "Succès"), ("warning", "Avertissement")], default="info", max_length=12)),
                ("link_label", models.CharField(blank=True, max_length=60)),
                ("link_url", models.URLField(blank=True, max_length=500)),
                ("starts_on", models.DateField()),
                ("ends_on", models.DateField(blank=True, null=True, help_text="Laisser vide pour une notification sans date de fin")),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["sort_order", "starts_on", "id"]},
        ),
    ]
