# Generated by Django 5.1.5 on 2025-02-01 13:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='bio',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='user',
            name='stage_name',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
