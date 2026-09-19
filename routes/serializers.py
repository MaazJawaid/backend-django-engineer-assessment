"""Request / response serializers for the routes API."""

from rest_framework import serializers


class LatLngSerializer(serializers.Serializer):
    lat = serializers.FloatField()
    lng = serializers.FloatField()


class OptimizeRouteSerializer(serializers.Serializer):
    start = serializers.JSONField()
    finish = serializers.JSONField()
    start_full_tank = serializers.BooleanField(required=False, default=True)
    corridor_miles = serializers.FloatField(required=False, default=5.0, min_value=0.1)

    def validate_start(self, value):
        return self._validate_location(value, "start")

    def validate_finish(self, value):
        return self._validate_location(value, "finish")

    def _validate_location(self, value, field_name):
        if isinstance(value, str):
            text = value.strip()
            if not text:
                raise serializers.ValidationError(f"{field_name} must be a non-empty string")
            return {"type": "text", "value": text}
        if isinstance(value, dict):
            lat = value.get("lat")
            lng = value.get("lng")
            if lat is None or lng is None:
                raise serializers.ValidationError(
                    f"{field_name} dict must include lat and lng"
                )
            try:
                lat_f = float(lat)
                lng_f = float(lng)
            except (TypeError, ValueError) as exc:
                raise serializers.ValidationError(
                    f"{field_name} lat/lng must be numbers"
                ) from exc
            if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
                raise serializers.ValidationError(
                    f"{field_name} lat/lng out of range"
                )
            return {"type": "coords", "lat": lat_f, "lng": lng_f, "value": f"{lat_f},{lng_f}"}
        raise serializers.ValidationError(
            f"{field_name} must be a string or {{lat, lng}} object"
        )
