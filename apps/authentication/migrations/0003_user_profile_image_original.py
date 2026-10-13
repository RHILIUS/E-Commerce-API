from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("authentication", "0002_remove_user_country_code_useraddress")]
    operations = [migrations.AddField(
        model_name="user", name="profile_image_original",
        field=models.ImageField(blank=True, upload_to="users/profile/originals/"),
    )]
