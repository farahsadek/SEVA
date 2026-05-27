# Calculates route between two locations using Google Maps Routes API

import os
import requests
from dotenv import load_dotenv
from .geocoding import geocode    

load_dotenv()


def get_route(origin: str, destination: str) -> dict | None:
    origin_coords = geocode(origin)
    if not origin_coords:
        print(f"[routing] Could not geocode origin: '{origin}'")
        return None

    dest_coords = geocode(destination)
    if not dest_coords:
        print(f"[routing] Could not geocode destination: '{destination}'")
        return None

    orig_lat, orig_lng = origin_coords
    dest_lat, dest_lng = dest_coords

    try:
        response = requests.post(
            "https://routes.googleapis.com/directions/v2:computeRoutes",
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": os.getenv("GOOGLE_MAPS_API_KEY"),
                "X-Goog-FieldMask": (
                    "routes.duration,"
                    "routes.distanceMeters,"
                    "routes.polyline.encodedPolyline"
                )
            },
            json={
                "origin": {
                    "location": {
                        "latLng": {
                            "latitude": orig_lat,
                            "longitude": orig_lng
                        }
                    }
                },
                "destination": {
                    "location": {
                        "latLng": {
                            "latitude": dest_lat,
                            "longitude": dest_lng
                        }
                    }
                },
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_AWARE",
            },
            timeout=5
        ).json()

        if "routes" not in response or not response["routes"]:
            print(f"[routing] No routes returned for '{origin}' → '{destination}'")
            return None

        route = response["routes"][0]
        distance_m = route.get("distanceMeters", 0)
        dur_str    = route.get("duration", "0s")
        duration_s = int(''.join(filter(str.isdigit, dur_str.split('.')[0]))) if any(c.isdigit() for c in dur_str) else 0
        polyline = route.get("polyline", {}).get("encodedPolyline")

        return {
            "distance_km":    round(distance_m / 1000, 1),
            "duration_min":   round(duration_s / 60, 0),
            "origin":         origin,
            "destination":    destination,
            "origin_coords":  (orig_lat, orig_lng),
            "dest_coords":    (dest_lat, dest_lng),
            "polyline":       polyline
        }

    except Exception as e:
        print(f"[routing] Exception: {e}")
        return None


# ─────────────────────────────────────────
# Main — Test
# ─────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("Routing Module Test")
    print("=" * 55)

    test_routes = [
        ("German University in Cairo", "Cairo Airport"),
        ("Point 90", "Cairo Festival City"),
        ("Maadi", "New Cairo"),
    ]

    for origin, destination in test_routes:
        print(f"\nRoute: {origin} → {destination}")
        route = get_route(origin, destination)

        if route:
            print(f"  Distance : {route['distance_km']} km")
            print(f"  Duration : {int(route['duration_min'])} minutes")
            print(f"  Polyline : {route['polyline'][:50]}...")
        else:
            print("  ✗ Route not found")

    # Edge case — invalid location
    print("\nEdge case — invalid location:")
    route = get_route("ThisPlaceDoesNotExist12345", "Cairo Airport")
    if route is None:
        print("  ✓ Correctly returned None for invalid origin")

    print("\n" + "=" * 55)
    print("Routing Test Complete")
    print("=" * 55)