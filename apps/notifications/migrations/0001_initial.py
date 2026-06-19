from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('message', models.TextField()),
                ('level', models.CharField(
                    choices=[('info', 'Info'), ('success', 'Success'), ('warning', 'Warning'), ('error', 'Error')],
                    default='info', max_length=20,
                )),
                ('icon', models.CharField(blank=True, default='fa-bell', max_length=60)),
                ('read', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
