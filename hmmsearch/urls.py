from django.urls import path
from . import views

urlpatterns = [
    path("", views.hmmsearch_form, name="hmmsearch_form"),
    path("dismiss-preload/", views.dismiss_preloaded_hmm, name="hmmsearch_dismiss_preload"),
    path('status/<int:project_id>/', views.hmmsearch_status, name='hmmsearch_status'),
    path('task-status/<str:task_id>/', views.hmmsearch_task_status, name='hmmsearch_task_status'),
    path('<int:project_id>/download-fasta/', views.hmmsearch_fasta_download, name='hmmsearch_fasta_download'),
    path('<int:project_id>/download-hmm-input/', views.hmmsearch_hmm_input_download, name='hmmsearch_hmm_input_download'),
    path('<int:project_id>/download-output/', views.hmmsearch_output_download, name='hmmsearch_output_download'),
    path('<int:project_id>/download-output/<str:file_type>/', views.hmmsearch_output_download, name='hmmsearch_output_download_typed'),
]
