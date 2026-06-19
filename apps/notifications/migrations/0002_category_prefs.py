from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('notifications', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='category',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
        migrations.CreateModel(
            name='NotificationPrefs',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('episodes_queued',   models.BooleanField(default=True)),
                ('movie_available',   models.BooleanField(default=True)),
                ('download_complete', models.BooleanField(default=True)),
                ('download_failed',   models.BooleanField(default=True)),
                ('file_moved',        models.BooleanField(default=True)),
                ('file_failed',       models.BooleanField(default=True)),
                ('storage_warning',   models.BooleanField(default=True)),
            ],
            options={'verbose_name': 'Notification Preferences'},
        ),
    ]
