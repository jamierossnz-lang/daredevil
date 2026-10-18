from django.db import migrations, models


def forwards(apps, schema_editor):
    # 'all' was the old built-in default — switch it to the new automatic
    # default (future episodes if still airing, none if ended). A different
    # choice someone made on the Settings page is left alone.
    ArrService = apps.get_model('arr', 'ArrService')
    ArrService.objects.filter(kind='sonarr', monitor='all').update(monitor='auto')


def backwards(apps, schema_editor):
    ArrService = apps.get_model('arr', 'ArrService')
    ArrService.objects.filter(kind='sonarr', monitor='auto').update(monitor='all')


class Migration(migrations.Migration):

    dependencies = [
        ('arr', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='arrservice',
            name='monitor',
            field=models.CharField(blank=True, help_text='Sonarr: auto/all/future/missing/…  Radarr: movieOnly/movieAndCollection/none', max_length=30),
        ),
        migrations.RunPython(forwards, backwards),
    ]
