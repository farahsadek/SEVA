# Converts place names to (lat, lng) coordinates using Google Maps Geocoding API

import os
import requests
from dotenv import load_dotenv

load_dotenv()

def geocode(place_name: str) -> tuple[float, float] | None:
    # If it's already coordinates, parse directly
    try:
        parts = place_name.strip().split(",")
        if len(parts) == 2:
            lat = float(parts[0].strip())
            lng = float(parts[1].strip())
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                print(f"[geocode] Using raw coordinates: ({lat}, {lng})")
                return (lat, lng)
    except (ValueError, AttributeError):
        pass

    # Otherwise geocode normally
    try:
        response = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={
                "address": place_name + ", Egypt",
                "key": os.getenv("GOOGLE_MAPS_API_KEY"),
                "region": "eg",
                "language": "en"
            },
            timeout=5
        ).json()

        if response.get("status") == "OK":
            result       = response["results"][0]
            result_types = result.get("types", [])

            # Reject country-level fallback
            # Google returns types=['country','political'] when place is unrecognized
            if "country" in result_types:
                print(f"[geocode] Rejected '{place_name}' → unrecognized place, Google returned country only")
                return None

            loc    = result["geometry"]["location"]
            coords = (loc["lat"], loc["lng"])
            print(f"[geocode] API result: '{place_name}' → {coords}")
            return coords

        print(f"[geocode] API returned status: {response.get('status')} for '{place_name}'")
        return None

    except Exception as e:
        print(f"[geocode] Exception for '{place_name}': {e}")
        return None

def geocode_or_coords(origin: str, profile: dict = None) -> tuple[float, float] | None:
    """If origin contains coordinates, parse them. Otherwise geocode normally."""
    # Check if it looks like coordinates
    if "lat=" in origin and "lng=" in origin:
        try:
            lat = float(origin.split("lat=")[1].split(",")[0])
            lng = float(origin.split("lng=")[1].split(")")[0].strip())
            return (lat, lng)
        except:
            pass
    return geocode(origin)