from django.db import migrations, models


def _seed_defaults(apps, schema_editor):
    QualityProfile = apps.get_model('media_tracker', 'QualityProfile')
    defaults = [
        ('1080p', 'tv',    500,   3000),
        ('2160p', 'tv',    3000,  20000),
        ('1080p', 'movie', 1000,  20000),
        ('2160p', 'movie', 15000, 80000),
    ]
    for quality, media_type, min_mb, max_mb in defaults:
        QualityProfile.objects.get_or_create(
            quality=quality,
            media_type=media_type,
            defaults={'min_size_mb': min_mb, 'max_size_mb': max_mb},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('media_tracker', '0003_episode_air_datetime_tvshow_tvmaze_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='tvshow',
            name='preferred_quality',
            field=models.CharField(
                choices=[('auto', 'Auto'), ('1080p', 'HD (1080p)'), ('2160p', '4K (2160p)')],
                default='auto',
                max_length=10,
            ),
        ),
        migrations.CreateModel(
            name='QualityProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quality', models.CharField(
                    choices=[('1080p', 'HD (1080p)'), ('2160p', '4K (2160p)')],
                    max_length=10,
                )),
                ('media_type', models.CharField(
                    choices=[('tv', 'TV'), ('movie', 'Movie')],
                    max_length=10,
                )),
                ('min_size_mb', models.PositiveIntegerField(blank=True, null=True)),
                ('max_size_mb', models.PositiveIntegerField(blank=True, null=True)),
            ],
            options={
                'unique_together': {('quality', 'media_type')},
            },
        ),
        migrations.RunPython(
            _seed_defaults,
            migrations.RunPython.noop,
        ),
    ]
