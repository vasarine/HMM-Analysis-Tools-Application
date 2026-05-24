from django.db import migrations, models
import django.db.models.deletion


def populate_snapshots(apps, schema_editor):
    """
    Back-fill snapshot fields for existing StepRun rows that still have a live
    step FK.  Rows whose step was already NULL (shouldn't normally exist, but
    defensive) are left with the default values (empty string / 0).
    """
    StepRun = apps.get_model('workflows', 'StepRun')
    to_update = []
    for sr in StepRun.objects.select_related('step').filter(step__isnull=False):
        sr.tool_type_snapshot = sr.step.tool_type
        sr.step_order_snapshot = sr.step.order
        to_update.append(sr)
    if to_update:
        StepRun.objects.bulk_update(to_update, ['tool_type_snapshot', 'step_order_snapshot'])


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0007_add_workflow_run_input'),
    ]

    operations = [
        # 1. Add snapshot columns (nullable / blank so existing rows are unaffected).
        migrations.AddField(
            model_name='steprun',
            name='tool_type_snapshot',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
        migrations.AddField(
            model_name='steprun',
            name='step_order_snapshot',
            field=models.IntegerField(default=0),
        ),
        # 2. Back-fill snapshot values from the live step FK while it still exists.
        migrations.RunPython(populate_snapshots, migrations.RunPython.noop),
        # 3. Change step FK from CASCADE to SET_NULL so that deleting a Workflow
        #    template (which cascades to WorkflowStep) no longer destroys
        #    completed run execution records.
        migrations.AlterField(
            model_name='steprun',
            name='step',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to='workflows.workflowstep',
            ),
        ),
    ]
