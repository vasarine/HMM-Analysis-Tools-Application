from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_alter_useractionhistory_action_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='useractionhistory',
            name='session_key',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddIndex(
            model_name='useractionhistory',
            index=models.Index(fields=['session_key', '-timestamp'], name='users_usera_session_cd6789_idx'),
        ),
    ]
