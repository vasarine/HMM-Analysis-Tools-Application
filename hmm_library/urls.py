from django.urls import path
from . import views

urlpatterns = [
    path('api/search/', views.search_hmm_autocomplete, name='hmm_search_autocomplete'),
    path('api/cache-stats/', views.get_cache_stats, name='hmm_cache_stats'),
]
