# Generated by Django 3.1 on 2021-03-17 19:33

from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('bills', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='statement',
            name='administration',
            field=models.CharField(blank=True, max_length=100, default='common'),
        ),
    ]
