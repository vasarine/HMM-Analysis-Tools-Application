from django.contrib.auth import views as auth_views
from django.contrib import admin
from django.urls import path, include
from .views import homepage
from users.views import register
from django.conf.urls.static import static
from django.conf import settings

urlpatterns = [
    path('', homepage, name='home'),
    path('register/', register, name='register'),
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='home'), name='logout'),

    path('password-reset/', auth_views.PasswordResetView.as_view(), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),

    path('users/', include('users.urls')),
    path('hmmsearch/', include('hmmsearch.urls')),
    path('hmmbuild/', include('hmmbuild.urls')),
    path("hmmemit/", include("hmmemit.urls")),
    path('hmm-library/', include('hmm_library.urls')),
    path('preprocessing/sequences/', include('sequence_tools.urls')),
    path('preprocessing/msa/', include('msa_tools.urls')),
    path('workflows/', include('workflows.urls')),

]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
