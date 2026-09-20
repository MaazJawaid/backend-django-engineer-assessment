from django.db import models


class FuelStop(models.Model):
    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    rack_id = models.IntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=10, decimal_places=4)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    geocode_source = models.CharField(max_length=20, default="gazetteer")

    class Meta:
        indexes = [
            models.Index(fields=["latitude", "longitude"]),
        ]
        db_table = "fuel_stop"

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state})"


class GeocodeCache(models.Model):
    query = models.CharField(max_length=255, unique=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    source = models.CharField(max_length=20, default="ors")
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "geocode_cache"

    def __str__(self):
        return self.query


class RouteCache(models.Model):
    start_lat = models.FloatField()
    start_lng = models.FloatField()
    finish_lat = models.FloatField()
    finish_lng = models.FloatField()
    geometry = models.JSONField()
    distance_miles = models.FloatField()
    start_text = models.CharField(max_length=255, blank=True)
    finish_text = models.CharField(max_length=255, blank=True)
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("start_lat", "start_lng", "finish_lat", "finish_lng")]
        db_table = "route_cache"

    def __str__(self):
        return f"Route {self.pk}: {self.start_text} -> {self.finish_text}"
