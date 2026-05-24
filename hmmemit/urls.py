from django.urls import path
from . import views

urlpatterns = [
    path("", views.hmmemit_form, name="hmmemit_form"),
    path('dismiss-preload/', views.hmmemit_dismiss_preload, name='hmmemit_dismiss_preload'),
    path('status/<int:project_id>/', views.hmmemit_status, name='hmmemit_status'),
    path('task-status/<str:task_id>/', views.hmmemit_task_status, name='hmmemit_task_status'),
    path('<int:project_id>/download-input/', views.hmmemit_input_download, name='hmmemit_input_download'),
    path('<int:project_id>/download-output/', views.hmmemit_output_download, name='hmmemit_output_download'),
]
