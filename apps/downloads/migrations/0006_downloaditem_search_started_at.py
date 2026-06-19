from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('downloads', '0005_filemove_media_pks'),
    ]

    operations = [
        migrations.AddField(
            model_name='downloaditem',
            name='search_started_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
