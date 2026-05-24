from django.db import migrations, models


def backfill_config_snapshot(apps, schema_editor):
    StepRun = apps.get_model('workflows', 'StepRun')
    to_update = []
    for sr in StepRun.objects.select_related('step').filter(step__isnull=False):
        sr.step_config_snapshot = sr.step.config or {}
        to_update.append(sr)
    if to_update:
        StepRun.objects.bulk_update(to_update, ['step_config_snapshot'])


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0008_steprun_snapshot_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='steprun',
            name='step_config_snapshot',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(backfill_config_snapshot, migrations.RunPython.noop),
    ]
