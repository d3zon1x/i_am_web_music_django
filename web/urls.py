from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from .views import root_info, link, send_song, history, logout, logout_alias

urlpatterns = [
    path('', root_info, name='root-info'),
    path('api/link', link, name='api-link'),
    path('api/send', send_song, name='api-send-song'),
    path('api/logout', logout, name='api-logout'),
    path('api/history', history, name='api-history'),
    path('logout', logout_alias, name='logout'),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]
