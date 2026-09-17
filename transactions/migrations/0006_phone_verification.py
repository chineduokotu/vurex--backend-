from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("transactions", "0005_dispute_defense_evidence_url_and_more")]

    operations = [
        migrations.AddField("user", "phone_verified_at", models.DateTimeField(blank=True, null=True)),
        migrations.AddField("user", "is_active", models.BooleanField(default=True)),
        migrations.AddField("user", "auth_version", models.PositiveIntegerField(default=0)),
        migrations.AddConstraint(
            "user", models.UniqueConstraint(
                fields=("phone",), condition=models.Q(phone_verified_at__isnull=False),
                name="unique_verified_user_phone",
            ),
        ),
        # Previously stored phone numbers are deliberately not marked verified.
    ]
