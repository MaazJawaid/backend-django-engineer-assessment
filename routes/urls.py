from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("routes/optimize/", views.OptimizeRouteView.as_view(), name="optimize-route"),
    path("routes/<int:pk>/map/", views.route_map, name="route-map"),
]
