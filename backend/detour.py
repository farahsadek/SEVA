"""
Calculates real driving detour distances for charging stations
using Google Routes API with parallel async requests.
"""
import os
import asyncio
import aiohttp
from dotenv import load_dotenv

load_dotenv()

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


async def _get_single_detour(
    session:        aiohttp.ClientSession,
    origin_coords:  tuple,
    station_coords: tuple,
    dest_coords:    tuple,
    station_id:     int
) -> tuple[int, float]:
    """
It makes one Google Routes API call for one station 
and returns the total driving distance of the route origin → station → destination.
Returns (station_id, total_route_km_via_station)
    """
    orig_lat, orig_lng = origin_coords
    sta_lat,  sta_lng  = station_coords
    dest_lat, dest_lng = dest_coords

    body = {
        "origin": {
            "location": {
                "latLng": {
                    "latitude":  orig_lat,
                    "longitude": orig_lng
                }
            }
        },
        "destination": {
            "location": {
                "latLng": {
                    "latitude":  dest_lat,
                    "longitude": dest_lng
                }
            }
        },
        "intermediates": [
            {
                "location": {
                    "latLng": {
                        "latitude":  sta_lat,
                        "longitude": sta_lng
                    }
                }
            }
        ],
        "travelMode":        "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }

    headers = {
        "Content-Type":     "application/json",
        "X-Goog-Api-Key":   GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.distanceMeters"
    }

    try:
        async with session.post(ROUTES_URL, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            data = await resp.json()

            if "routes" in data and data["routes"]:
                total_km = data["routes"][0]["distanceMeters"] / 1000
                return (station_id, round(total_km, 2))
            else:
                print(f"[detour] No route returned for station {station_id}")
                return (station_id, None)

    except Exception as e:
        print(f"[detour] Error for station {station_id}: {e}")
        return (station_id, None)


async def _fetch_all_detours(
    stations:         list,
    origin_coords:    tuple,
    dest_coords:      tuple,
    route_distance_km: float
) -> dict[int, float]:
    """
    Fetch detours for all stations in parallel.
    All stations in parallel, shared session
    Returns dict of {station_id: detour_km}
    """
    async with aiohttp.ClientSession() as session:
        tasks = [
            _get_single_detour(
                session,
                origin_coords,
                (s["lat"], s["lng"]),
                dest_coords,
                s["id"]
            )
            for s in stations
        ]
        results = await asyncio.gather(*tasks)

    detours = {}
    for station_id, total_km in results:
        if total_km is not None:
            detour_km = max(0, total_km - route_distance_km)
            detours[station_id] = round(detour_km, 2)
            print(f"[detour] Station {station_id}: total={total_km}km, detour={detour_km:.2f}km")
        else:
            # Fallback to 0 if API call failed
            detours[station_id] = 0
            print(f"[detour] Station {station_id}: fallback to 0 detour")

    return detours

def get_station_detours(
    stations:          list,
    origin_coords:     tuple,
    dest_coords:       tuple,
    route_distance_km: float
) -> dict[int, float]:
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(
                asyncio.run,
                _fetch_all_detours(stations, origin_coords, dest_coords, route_distance_km)
            )
            return future.result()
    except RuntimeError:
        # No running loop — safe to use asyncio.run directly
        return asyncio.run(
            _fetch_all_detours(stations, origin_coords, dest_coords, route_distance_km)
        )