from django.urls import path
from . import views

urlpatterns = [
    path('',                             views.workflow_list,         name='workflow_list'),
    path('new/',                         views.workflow_builder,      name='workflow_builder_new'),
    path('<int:workflow_id>/edit/',      views.workflow_builder,      name='workflow_builder_edit'),
    path('<int:workflow_id>/run/',       views.workflow_run_start,    name='workflow_run_start'),
    path('api/save/',                    views.save_workflow,         name='workflow_save'),
    path('api/builder-state/',           views.builder_state,         name='workflow_builder_state'),
    path('runs/<int:run_id>/',           views.workflow_run_status,   name='workflow_run_status'),
    path('runs/<int:run_id>/retry/',     views.workflow_run_retry,    name='workflow_run_retry'),
    path('runs/<int:run_id>/poll/',      views.workflow_run_poll,     name='workflow_run_poll'),
    path('runs/<int:run_id>/result/',    views.workflow_run_result,   name='workflow_run_result'),
    path('runs/<int:run_id>/download-input/', views.workflow_run_input_download, name='workflow_run_input_download'),
    path('runs/<int:run_id>/download/',  views.workflow_run_download, name='workflow_run_download'),
    path('runs/<int:run_id>/step/<int:step_order>/download/', views.workflow_step_download, name='workflow_step_download'),
    path('runs/<int:run_id>/delete/',    views.delete_workflow_run,   name='delete_workflow_run'),
    path('<int:workflow_id>/delete/',    views.delete_workflow,       name='delete_workflow'),
    path('<int:workflow_id>/export/',    views.workflow_export,       name='workflow_export'),
    path('import/',                      views.workflow_import,       name='workflow_import'),
]
