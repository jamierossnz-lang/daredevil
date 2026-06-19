from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('media_tracker', '0002_add_monitor_from'),
    ]

    operations = [
        migrations.AddField(
            model_name='tvshow',
            name='tvmaze_id',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='episode',
            name='air_datetime',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text='Precise air datetime in UTC from TVMaze. Use timezone.localtime() to display in NZT.',
            ),
        ),
    ]
