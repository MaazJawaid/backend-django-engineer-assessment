# Fuel-Optimal Route API

Django REST API that plans a driving route between two USA locations and selects **cost-optimal fuel stops** along the way, given a vehicle with a **500-mile range** and **10 MPG**.

## Features

- Geocode start/finish (or accept lat/lng) via **OpenRouteService / HeiGIT**
- One directions call per unique route (results cached in SQLite)
- Match nearby truck stops from the provided OPIS fuel-price CSV
- Greedy min-cost refueling algorithm (provably optimal for known prices)
- GeoJSON response + interactive **Leaflet / OpenStreetMap** map page
- Offline fuel-stop geocoding via a bundled **GeoNames** US cities gazetteer (CC0)

## Requirements

- Python 3.10+
- Free [HeiGIT / OpenRouteService](https://account.heigit.org/) API key

## Setup

```bash
# 1. Clone / enter the project directory
cd backend-django-engineer-assessment

# 2. Create a virtualenv (recommended)
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env   # Windows
# cp .env.example .env   # macOS/Linux
# Edit .env and set ORS_API_KEY=your_key

# 5. Migrate & load data
python manage.py migrate
# Gazetteer is already bundled under data/; rebuild with:
#   python manage.py build_gazetteer
python manage.py load_fuel_stops
# Optional: geocode city/state misses via ORS (slow, rate-limited):
#   python manage.py load_fuel_stops --geocode-misses

# 6. Run
python manage.py runserver
```

## API

### `GET /api/health/`

```json
{"status": "ok"}
```

### `POST /api/routes/optimize/`

**Request**

```json
{
  "start": "Chicago, IL",
  "finish": "Dallas, TX",
  "start_full_tank": true,
  "corridor_miles": 5
}
```

`start` / `finish` may also be coordinates:

```json
{"start": {"lat": 41.8781, "lng": -87.6298}, "finish": {"lat": 32.7767, "lng": -96.7970}}
```

**Response (200)**

```json
{
  "route_id": 1,
  "route": {"type": "LineString", "coordinates": [[-87.62, 41.87], "..."]},
  "distance_miles": 960.5,
  "fuel_stops": [
    {
      "opis_id": 7,
      "name": "WOODSHED OF BIG CABIN",
      "city": "Big Cabin",
      "state": "OK",
      "latitude": 36.8,
      "longitude": -95.2,
      "mile_marker": 412.3,
      "retail_price": "3.0073",
      "gallons": "25.00",
      "cost": "75.18"
    }
  ],
  "total_cost": "312.45",
  "total_gallons": "46.05",
  "map_url": "/api/routes/1/map/",
  "assumptions": {
    "range_miles": 500,
    "mpg": 10,
    "start_full_tank": true,
    "corridor_miles": 5
  }
}
```

**Errors**

| Status | Meaning |
|--------|---------|
| 400 | Invalid validation / geocode failure |
| 422 | Infeasible (gap > 500 mi) or no driving route |
| 502 | OpenRouteService unreachable / quota |

### `GET /api/routes/{id}/map/`

HTML page with Leaflet + OSM tiles showing the route polyline and planned fuel stops.

## Example curl

```bash
curl -X POST http://127.0.0.1:8000/api/routes/optimize/ ^
  -H "Content-Type: application/json" ^
  -d "{\"start\": \"Chicago, IL\", \"finish\": \"Dallas, TX\"}"
```

Then open `http://127.0.0.1:8000/api/routes/<route_id>/map/` in a browser.

## Assumptions

| Parameter | Value | Notes |
|-----------|-------|-------|
| Range | 500 miles | Assignment constraint |
| MPG | 10 | Assignment constraint → 50 gal tank |
| `start_full_tank` | `true` (default) | Initial 50 gal bought *before* the trip is **not** counted in `total_cost` |
| Price per station | Min retail price | CSV has multiple grades per OPIS ID; we keep the cheapest |
| Stop coordinates | City center | Joined from GeoNames gazetteer by City/State (approximation) |
| Corridor | 5 miles (default) | Max distance off-route to consider a stop |

With `start_full_tank=true`, gallons purchased en route ≈ `(distance − 500) / 10` (zero if distance ≤ 500).

## Algorithm

Classic **greedy optimal refueling**:

1. Feasibility check: any gap between consecutive stations (or start/end) > 500 mi → infeasible.
2. At the current position, look at stations reachable within remaining fuel.
3. If a **cheaper** station is reachable → buy just enough to reach the **nearest** cheaper one.
4. Otherwise → **fill the tank** and drive to the **farthest** reachable station.
5. Repeat until the destination is reachable with remaining fuel.

This is the standard optimal strategy for the single-tank, known-prices gas-station problem.

## External APIs (rate limits)

| Service | Host | Calls per optimize request |
|---------|------|----------------------------|
| Geocoding | `api.heigit.org/pelias/v1` | 0–2 (cached) |
| Directions | `api.heigit.org/openrouteservice/v2/directions/driving-car/geojson` | 0–1 (cached) |

Identical start/finish pairs reuse `GeocodeCache` and `RouteCache` — **zero** ORS calls on cache hits.

Do **not** use the deprecated `api.openrouteservice.org` host (shut off 2026-09-28).

## Data sources

- **Fuel prices**: `fuel-prices-for-be-assessment.csv` (provided)
- **City coordinates**: GeoNames [`cities1000`](https://download.geonames.org/export/dump/) filtered to US — CC0 / public domain (`data/us_cities_gazetteer.csv`)

## Tests

```bash
python manage.py test routes -v 2
```

## Project layout

```
fuel_route/          # Django project settings
routes/
  models.py          # FuelStop, GeocodeCache, RouteCache
  views.py           # optimize + map endpoints
  services/
    ors_client.py    # HeiGIT API client
    geo.py           # haversine, corridor matching
    fuel_optimizer.py
  management/commands/
    build_gazetteer.py
    load_fuel_stops.py
  templates/routes/map.html
data/us_cities_gazetteer.csv
```
