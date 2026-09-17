from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from notifications.views import termii_webhook


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("identity.urls")),
    path("api/webhooks/termii/", termii_webhook, name="termii-webhook"),
    path("api/", include("transactions.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
