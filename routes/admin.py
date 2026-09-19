from django.contrib import admin

from .models import FuelStop, GeocodeCache, RouteCache


@admin.register(FuelStop)
class FuelStopAdmin(admin.ModelAdmin):
    list_display = ("opis_id", "name", "city", "state", "retail_price", "geocode_source")
    list_filter = ("state", "geocode_source")
    search_fields = ("name", "city", "opis_id")


@admin.register(GeocodeCache)
class GeocodeCacheAdmin(admin.ModelAdmin):
    list_display = ("query", "latitude", "longitude", "source", "fetched_at")
    search_fields = ("query",)


@admin.register(RouteCache)
class RouteCacheAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "start_text",
        "finish_text",
        "distance_miles",
        "fetched_at",
    )
    search_fields = ("start_text", "finish_text")
