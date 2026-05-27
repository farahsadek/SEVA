# Fetches EV charging stations from Open Charge Map API

import os
import requests
from dotenv import load_dotenv
from .geocoding import geocode     # relative import
from .routing import get_route     # relative import

load_dotenv()


def get_stations_along_route(
    origin: str,
    destination: str,
    search_radius_km: int = 5
) -> tuple[list, dict | None]:
    """
    Find charging stations along the driving route from origin to destination.

    Fallback chain:
      1. Polyline search along the full route (best)
      2. Point search at route midpoint (if no polyline)
      3. Point search near origin (if no route at all)

    Returns:
        (stations list, route dict or None)
    """
    print(f"[stations] Fetching route: '{origin}' → '{destination}'")
    route = get_route(origin, destination)

    # ── No route: search near origin ────────────────────────
    if not route:
        print("[stations] No route — searching near origin")
        origin_coords = geocode(origin)
        if not origin_coords:
            print("[stations] Could not geocode origin either")
            return [], None
        lat, lng = origin_coords
        return get_stations_near_point(lat, lng), None

    polyline = route.get("polyline")

    # ── No polyline: search at midpoint ─────────────────────
    if not polyline:
        print("[stations] No polyline — searching at route midpoint")
        orig_lat, orig_lng = route["origin_coords"]
        dest_lat, dest_lng = route["dest_coords"]
        mid_lat = (orig_lat + dest_lat) / 2
        mid_lng = (orig_lng + dest_lng) / 2
        return get_stations_near_point(mid_lat, mid_lng), route

    # ── Polyline search (primary path) ──────────────────────
    try:
        data = requests.get(
            "https://api.openchargemap.io/v3/poi",
            params={
                "polyline":     polyline,
                "distance":     search_radius_km,
                "distanceunit": "KM",
                "maxresults":   10,
                "countrycode":  "EG",
                "statustypeid": 50,
                "compact":      False,
                "verbose":      False,
                "key":          os.getenv("OPENCHARGE_MAP_API_KEY")
            },
            timeout=10
        ).json()

        stations = parse_stations(data)

        if not stations:
            print("[stations] No stations along route — falling back to origin point")
            orig_lat, orig_lng = route["origin_coords"]
            stations = get_stations_near_point(orig_lat, orig_lng)

        return stations, route

    except Exception as e:
        print(f"[stations] Polyline search error: {e}")
        return [], route


def get_stations_near_point(lat: float, lng: float) -> list:
    try:
        data = requests.get(
            "https://api.openchargemap.io/v3/poi",
            params={
                "latitude":     lat,
                "longitude":    lng,
                "maxresults":   10,
                "countrycode":  "EG",
                "statustypeid": 50,
                "distance":     10,
                "distanceunit": "KM",
                "compact":      False,
                "verbose":      False,
                "key":          os.getenv("OPENCHARGE_MAP_API_KEY")
            },
            timeout=5
        ).json()

        return parse_stations(data)

    except Exception as e:
        print(f"[stations] Point search error: {e}")
        return []

OPERATOR_NAMES = {
    3744: "IKARUS",
    3742: "Infinity EV",
    3293: "Revolta",
    3743: "Sha7en",
    3802: "Elsewedy Plug",
    3883: "Electra EG",
    3837: "Karm EG",
    3991: "MegaPlug",
    3920: "Electric Mobility",
    3803: "TAQA Volt",
}

def get_operator_name(operator_id: int) -> str:
    return OPERATOR_NAMES.get(operator_id, "Unknown Operator")

def parse_stations(data: list):
    stations = []

    for s in data:
        info = s.get("AddressInfo", {})
        station_lat = info.get("Latitude")
        station_lng = info.get("Longitude")

        if not station_lat or not station_lng:
            continue

        status = s.get("StatusType") or {}
        usage  = s.get("UsageType")  or {}
        operator_info = s.get("OperatorInfo") or {}        
        operator_id = operator_info.get("ID")
        print(f"Station: {info.get('Title')} | OperatorID: {operator_id}")
        operator_name = get_operator_name(operator_id) if operator_id else "Unknown Operator"
        # Parse each connector with full details
        connections = s.get("Connections") or []
        connectors_list = []  
        max_power = 0
        has_fast_charge = False
        
        for conn in connections:
            # Get connection-level status (overrides station status if present)
            conn_status = conn.get("StatusType") or {}
            is_operational = conn_status.get("IsOperational")
        
            # Get level info
            level = conn.get("Level") or {}
            level_title = level.get("Title", "Unknown")
            is_fast_charge_capable = level.get("IsFastChargeCapable", False)
            
            # Get current type
            current_type = conn.get("CurrentType") or {}
            current_type_title = current_type.get("Description", "Unknown")
            
            connection_type = conn.get("ConnectionType") or {}
            connection_type_title = connection_type.get("Title", "Unknown")
            
            # Power in KW
            power_kw = conn.get("PowerKW") or 0
            if power_kw and power_kw > max_power:
                max_power = power_kw
            if is_fast_charge_capable or power_kw > 22:
                has_fast_charge = True
            
            # Quantity (how many ports of this type)
            quantity = conn.get("Quantity") or 1
            
            connector_detail = {
                "connection_type_title": connection_type_title,
                "power_kw": power_kw,
                "current_type_title": current_type_title,
                "level_title": level_title,
                "is_fast_charge_capable": is_fast_charge_capable,
                "is_operational": is_operational,
                "quantity": quantity,
            }
            connectors_list.append(connector_detail)

        stations.append({
            "id": s.get("ID"),
            "name": info.get("Title", "Unknown Station"),
            "address": info.get("AddressLine1", ""),
            "address2": info.get("AddressLine2", ""),
            "town": info.get("Town", ""),
            "lat": station_lat,
            "lng": station_lng,
            "connectors": connectors_list,
            "operator": operator_name,
            # Station-level aggregates
            "max_power_kw": max_power,
            "total_ports": sum(c.get("quantity", 1) for c in connectors_list),
            "has_fast_charge": has_fast_charge,
            
            "available": status.get("IsOperational", True),
            "needs_membership": usage.get(
                "IsMembershipRequired", False
            ),
            "pay_at_location": usage.get(
                "IsPayAtLocation", False
            ),
            "usage_cost": s.get("UsageCost", "Unknown")
        })
    print(f"[stations] Parsed {len(stations)} stations from API response")
    return stations