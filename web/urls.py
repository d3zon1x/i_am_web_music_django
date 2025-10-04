from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from .views import root_info, link, send_song, history, logout, logout_alias, charts, favorites, get_user_by_token

urlpatterns = [
    path('', root_info, name='root-info'),
    path('api/link', link, name='api-link'),
    path('api/send', send_song, name='api-send-song'),
    path('api/logout', logout, name='api-logout'),
    path('api/history', history, name='api-history'),
    path('api/favorites', favorites, name='api-favorites'),
    path('api/user_by_token', get_user_by_token, name='api-user-by-token'),
    path('api/charts', charts, name='api-charts'),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]
