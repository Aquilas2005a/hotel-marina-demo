from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0026_newslettersubscription"),
    ]

    operations = [
        migrations.CreateModel(
            name="SocialLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("platform", models.CharField(choices=[("facebook", "Facebook"), ("instagram", "Instagram"), ("youtube", "YouTube"), ("tiktok", "TikTok"), ("linkedin", "LinkedIn"), ("whatsapp", "WhatsApp"), ("other", "Autre")], max_length=16)),
                ("label", models.CharField(blank=True, max_length=60)),
                ("url", models.URLField(max_length=500)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["sort_order", "platform", "id"]},
        ),
    ]
