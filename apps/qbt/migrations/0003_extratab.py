from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('qbt', '0002_categorypath'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExtraTab',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label', models.CharField(max_length=100)),
                ('path', models.CharField(max_length=1000)),
            ],
            options={
                'verbose_name': 'Extra File Browser Tab',
                'ordering': ['label'],
            },
        ),
    ]
