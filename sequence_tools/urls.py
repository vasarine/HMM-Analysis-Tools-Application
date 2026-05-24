from django.urls import path
from . import views

urlpatterns = [
    path('validate/', views.fasta_validate_form, name='fasta_validate'),
    path('validate/<int:project_id>/', views.fasta_validate_result, name='fasta_validate_result'),
    path('validate/<int:project_id>/download-input/', views.fasta_validate_input_download, name='fasta_validate_input_download'),
    path('clean/', views.fasta_clean_form, name='fasta_clean'),
    path('clean/dismiss-preload/', views.fasta_clean_dismiss_preload, name='fasta_clean_dismiss_preload'),
    path('clean/<int:project_id>/', views.fasta_clean_result, name='fasta_clean_result'),
    path('clean/<int:project_id>/download-input/', views.fasta_clean_input_download, name='fasta_clean_input_download'),
    path('clean/<int:project_id>/download/', views.fasta_clean_download, name='fasta_clean_download'),
]
