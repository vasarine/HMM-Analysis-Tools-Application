from django.urls import path
from . import views

urlpatterns = [
    path('my-projects/', views.my_projects, name='my-projects'),
    path('projects/<int:pk>/delete/', views.delete_project, name='delete_project'),
    path("delete-selected/", views.delete_selected_projects, name="delete_selected_projects"),

    path('projects/<str:tool>/<int:pk>/share/', views.share_project, name='share-project'),
    path('projects/<str:tool>/<int:pk>/remove-shared/', views.remove_shared_project, name='remove-shared-project'),
    path('shared/<str:tool>/<uuid:token>/', views.shared_project_view, name='shared-project'),
    path('public-projects/', views.public_projects, name='public-projects'),

    path('api/history/', views.get_user_history, name='user-history'),
    path('api/history/<int:history_id>/delete/', views.delete_history_entry, name='delete-history-entry'),
    path('api/history/clear/', views.clear_user_history, name='clear-user-history'),
]
