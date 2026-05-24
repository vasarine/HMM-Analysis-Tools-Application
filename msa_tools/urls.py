from django.urls import path
from . import views

urlpatterns = [
    path('align/', views.clustalo_form, name='clustalo_form'),
    path('align/dismiss-preload/', views.clustalo_dismiss_preload, name='clustalo_dismiss_preload'),
    path('align/<int:project_id>/status/', views.clustalo_status, name='clustalo_status'),
    path('align/task/<str:task_id>/', views.clustalo_task_status, name='clustalo_task_status'),
    path('align/<int:project_id>/download-input/', views.clustalo_input_download, name='clustalo_input_download'),
    path('align/<int:project_id>/download/', views.clustalo_download, name='clustalo_download'),
    path('convert/', views.format_convert_form, name='format_convert_form'),
    path('convert/dismiss-preload/', views.format_convert_dismiss_preload, name='format_convert_dismiss_preload'),
    path('convert/<int:project_id>/', views.format_convert_result, name='format_convert_result'),
    path('convert/<int:project_id>/download-input/', views.format_convert_input_download, name='format_convert_input_download'),
    path('convert/<int:project_id>/download/', views.format_convert_download, name='format_convert_download'),
]
