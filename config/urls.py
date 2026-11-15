from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Sprout Tote Co. — Owner"
admin.site.site_title = "Sprout Tote Co."
admin.site.index_title = "Run the business"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("rentals.urls")),
]
