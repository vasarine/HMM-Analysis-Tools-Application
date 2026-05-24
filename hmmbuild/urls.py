from django.urls import path
from .views import (
    hmmbuild_form, hmmbuild_dismiss_preload, hmmbuild_status,
    hmmbuild_task_status, hmmbuild_input_download, hmmbuild_output_download,
    hmmbuild_annotated_msa_download,
)

urlpatterns = [
    path('', hmmbuild_form, name='hmmbuild_form'),
    path('dismiss-preload/', hmmbuild_dismiss_preload, name='hmmbuild_dismiss_preload'),
    path('status/<int:project_id>/', hmmbuild_status, name='hmmbuild_status'),
    path('task-status/<str:task_id>/', hmmbuild_task_status, name='hmmbuild_task_status'),
    path('<int:project_id>/download-input/', hmmbuild_input_download, name='hmmbuild_input_download'),
    path('<int:project_id>/download-output/', hmmbuild_output_download, name='hmmbuild_output_download'),
    path('<int:project_id>/download-annotated/', hmmbuild_annotated_msa_download, name='hmmbuild_annotated_download'),
]
