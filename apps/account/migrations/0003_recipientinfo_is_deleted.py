from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("account", "0002_initial")]

    operations = [
        migrations.AddField(
            model_name="recipientinfo", name="is_deleted",
            field=models.BooleanField(default=False),
        ),
        migrations.AddConstraint(
            model_name="recipientinfo",
            constraint=models.CheckConstraint(
                condition=models.Q(is_deleted=False) | models.Q(is_default=False),
                name="deleted_recipient_not_default",
            ),
        ),
    ]
