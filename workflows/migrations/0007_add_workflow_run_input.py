from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0006_workflowrun_secondary_file_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='workflowrun',
            name='input_file',
            field=models.FileField(upload_to='workflows/input/', blank=True),
        ),
        migrations.CreateModel(
            name='WorkflowRunInput',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(max_length=64)),
                ('step_index', models.IntegerField(default=-1)),
                ('data_type', models.CharField(max_length=32)),
                ('file', models.FileField(upload_to='workflows/inputs/', null=True, blank=True)),
                ('accession', models.CharField(max_length=64, blank=True, default='')),
                ('workflow_run', models.ForeignKey(
                    to='workflows.workflowrun',
                    on_delete=models.deletion.CASCADE,
                    related_name='inputs',
                )),
            ],
            options={
                'ordering': ['step_index', 'role'],
                'unique_together': {('workflow_run', 'role')},
            },
        ),
    ]
