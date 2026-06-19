from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('downloads', '0004_filemove'),
    ]

    operations = [
        migrations.AddField(
            model_name='filemove',
            name='movie_pk',
            field=models.IntegerField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='filemove',
            name='episode_pk',
            field=models.IntegerField(null=True, blank=True),
        ),
    ]
