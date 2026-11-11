from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Moving Bins — Owner"
admin.site.site_title = "Moving Bins"
admin.site.index_title = "Run the business"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("rentals.urls")),
]
