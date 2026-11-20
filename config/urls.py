from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

admin.site.site_header = "Sprout Tote Co. — Owner"
admin.site.site_title = "Sprout Tote Co."
admin.site.index_title = "Run the business"

urlpatterns = [
    path("healthz", lambda request: HttpResponse("ok", content_type="text/plain")),  # host's uptime check
    path("admin/", admin.site.urls),
    path("", include("rentals.urls")),
]
