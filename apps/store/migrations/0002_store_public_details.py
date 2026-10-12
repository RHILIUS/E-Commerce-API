from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("store", "0001_initial")]
    operations = [
        migrations.AddField(model_name="store", name="public_email", field=models.EmailField(blank=True, max_length=254, help_text="Business email visible to all customers.")),
        migrations.AddField(model_name="store", name="public_phone", field=models.CharField(blank=True, max_length=40, help_text="Business phone visible to all customers.")),
    ]
