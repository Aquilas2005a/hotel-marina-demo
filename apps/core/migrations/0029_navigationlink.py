from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0028_sitenotice"),
    ]

    operations = [
        migrations.CreateModel(
            name="NavigationLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(max_length=60)),
                ("url", models.CharField(help_text="Chemin local (/restaurant/, #chambres) ou adresse externe HTTPS", max_length=500)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["sort_order", "id"]},
        ),
    ]
