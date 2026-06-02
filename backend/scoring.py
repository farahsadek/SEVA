import math
from data.ev_helper import (
    AC_MULTIPLIERS,
    is_station_compatible,
    get_effective_charge_speed,
    estimate_charge_time,
    calculate_target_charge,
    get_car_specs,
)
from .profile import is_station_liked, is_station_avoided, get_active_preference
from .detour import get_station_detours


def _haversine(lat1, lng1, lat2, lng2):
    R = 6371
    lat1, lng1, lat2, lng2 = map(math.radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a    = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def score_and_rank_stations(stations, profile, route, battery_level, charging_mode="charge_to_80"):
    car_model         = profile["car_model"]
    max_detour_km     = profile["max_detour_km"]
    price_sensitivity = profile["price_sensitivity"]
    print(f"[scoring] price_sensitivity value received: '{price_sensitivity}'")
    ac_usage          = profile["ac_usage"]
    min_threshold     = profile["min_battery_threshold"]
    saved_dests       = profile.get("saved_destinations", [])

    dest_lat          = route["dest_coords"][0]
    dest_lng          = route["dest_coords"][1]
    origin_lat        = route["origin_coords"][0]
    origin_lng        = route["origin_coords"][1]
    route_distance_km = route.get("distance_km", 0)

    try:
        specs = get_car_specs(car_model)
    except ValueError:
        raise ValueError(f"Car model '{car_model}' not found in EV database.")

    car_max_dc_kw = specs["max_dc_kw"]
    car_max_ac_kw = specs["max_ac_kw"]

    effective_range_check = specs["range_km"] * AC_MULTIPLIERS.get(ac_usage, 0.92)

    if charging_mode == "charge_to_80":
        global_target = 80
        if battery_level >= 80:
            raise ValueError("Your battery is already at 80% or above. No charging needed!")
    elif charging_mode == "charge_to_100":
        global_target = 100
        if battery_level == 100:
            raise ValueError("Your battery is already full. No charging needed!")
    elif charging_mode == "complete_trip":
        energy_needed_pct = (route_distance_km / effective_range_check) * 100
        usable_pct        = battery_level - min_threshold
        if usable_pct >= energy_needed_pct:
            raise ValueError(
                f"Your current battery ({battery_level}%) is enough to complete this "
                f"{round(route_distance_km)}km trip. No charging needed!"
            )
        global_target = None
    else:
        global_target = 80

    # ── STEP 1 — HARD FILTERS ─────────────────────────────────────────────────
    filtered           = []
    rejected_range     = 0
    rejected_connector = 0
    rejected_avoided   = 0

    effective_range   = specs["range_km"] * AC_MULTIPLIERS.get(ac_usage, 0.92)

    # If battery is already below threshold, we're in emergency mode
    if battery_level <= min_threshold:
        battery_available = max(1, battery_level - 5)
        print(f"[scoring] EMERGENCY: battery ({battery_level}%) is at or below threshold ({min_threshold}%) — using emergency range")
    else:
        battery_available = battery_level - min_threshold

    range_available   = (battery_available / 100) * effective_range * 0.9
    print(f"[scoring] battery_level={battery_level}, min_threshold={min_threshold}, battery_available={battery_available}%, range_available={round(range_available, 1)}km")

    for station in stations:
        if is_station_avoided(profile, station["id"]):
            rejected_avoided += 1
            continue

        has_compatible = False
        for connector in station["connectors"]:
            if is_station_compatible(car_model, connector["connection_type_title"], connector["power_kw"]):
                has_compatible = True
                break
        if not has_compatible:
            rejected_connector += 1
            continue

        distance_to_station = _haversine(origin_lat, origin_lng, station["lat"], station["lng"])
        if distance_to_station > range_available:
            rejected_range += 1
            continue

        filtered.append(station)

    if not filtered:
        total = len(stations)
        if rejected_range == total:
            if battery_level <= min_threshold:
                raise ValueError(
                    f"Your battery is critically low at {battery_level}%. "
                    f"None of the charging stations are close enough to reach safely. "
                    f"Please find the nearest charging point immediately or call roadside assistance."
                )
            else:
                raise ValueError(
                    f"Your battery is too low to reach any charging station safely. "
                    f"At {battery_level}% with a {min_threshold}% minimum threshold, "
                    f"you have {max(0, battery_level - min_threshold)}% usable charge — "
                    f"not enough to reach any station along this route. "
                    f"Please charge to at least {min_threshold + 15}% before attempting this trip."
                )
        elif rejected_connector == total:
            raise ValueError(
                f"None of the charging stations along this route are compatible "
                f"with your {car_model}. This may be due to connector type or power level mismatch."
            )
        elif rejected_avoided == total:
            raise ValueError(
                "All stations along this route are in your avoided list. "
                "Try clearing some avoided stations or choosing a different route."
            )
        elif rejected_range > rejected_connector:
            raise ValueError(
                f"Your battery is too low to safely reach any charging station. "
                f"At {battery_level}% with a {min_threshold}% minimum threshold, "
                f"you only have {max(0, battery_level - min_threshold)}% usable charge. "
                f"Please charge to at least {min_threshold + 15}% before this trip "
                f"({rejected_connector} station(s) also had incompatible connectors)."
            )
        else:
            raise ValueError(
                f"No suitable stations found — "
                f"{rejected_connector} station(s) had incompatible connectors, "
                f"{rejected_range} were out of range, "
                f"and {rejected_avoided} were in your avoided list."
            )

    # ── Hard filter: low cost → AC-only stations ──────────────────────────────
    if price_sensitivity == "low cost":
        ac_compatible = [
            s for s in filtered
            if any(
                c["power_kw"] <= 22 and is_station_compatible(car_model, c["connection_type_title"], c["power_kw"])
                for c in s["connectors"]
            )
        ]
        if ac_compatible:
            filtered = ac_compatible
            print(f"[scoring] Low cost filter: kept {len(filtered)} AC-compatible stations (DC excluded)")
        else:
            print(f"[scoring] Low cost filter: no AC stations found — keeping all {len(filtered)} as fallback")

    print(f"[scoring] Fetching real detours for {len(filtered)} stations...")
    try:
        detours = get_station_detours(
            filtered,
            (origin_lat, origin_lng),
            (dest_lat, dest_lng),
            route_distance_km
        )
        print(f"[scoring] Detours fetched: {detours}")
    except Exception as e:
        print(f"[scoring] Detour calculation failed: {e} — using 0 detour for all stations")
        detours = {s["id"]: 0 for s in filtered}

    # ── Hard filter: remove stations that exceed max_detour_km ───────────────
    within_detour = [s for s in filtered if detours.get(s["id"], 0) <= max_detour_km]
    if within_detour:
        filtered = within_detour
        print(f"[scoring] Detour hard filter ({max_detour_km}km): {len(filtered)} stations remain")
    else:
        print(f"[scoring] No stations within {max_detour_km}km detour limit — keeping all {len(filtered)} as fallback")

    # ── STEP 2 — SCORE EACH STATION ───────────────────────────────────────────
    scored = []

    for station in filtered:
        score       = 0
        station_lat = station["lat"]
        station_lng = station["lng"]

        # Criterion 1: Detour (25 pts)
        detour_km    = detours.get(station["id"], 0)
        detour_score = 25 * (1 - min(detour_km, max_detour_km) / max_detour_km) if max_detour_km > 0 else 0
        score       += detour_score
        station["detour_km"] = round(detour_km, 2)  # ← stored for llm.py to read

        dist_station_to_dest = _haversine(station_lat, station_lng, dest_lat, dest_lng)

        # Criterion 2: Find best connector
        best_connector = None
        best_power_kw  = 0
        best_is_dc     = False

        for connector in station["connectors"]:
            if not is_station_compatible(car_model, connector["connection_type_title"], connector["power_kw"]):
                continue
            power_kw  = connector["power_kw"]
            is_dc     = power_kw > 22
            effective = get_effective_charge_speed(car_model, power_kw, is_dc)
            if effective > best_power_kw:
                best_power_kw  = effective
                best_connector = connector["connection_type_title"]
                best_is_dc     = is_dc

        if global_target is not None:
            target_battery = global_target
        else:
            # complete_trip mode: account for energy spent reaching the station
            dist_origin_to_station = _haversine(origin_lat, origin_lng, station_lat, station_lng)
            ct_effective_range     = specs["range_km"] * AC_MULTIPLIERS.get(ac_usage, 0.92)
            energy_to_station_pct  = (dist_origin_to_station / ct_effective_range) * 100
            battery_at_station     = max(min_threshold, battery_level - energy_to_station_pct)
            energy_to_dest_pct     = (dist_station_to_dest / ct_effective_range) * 100
            target_battery         = battery_at_station + energy_to_dest_pct + min_threshold
            target_battery         = min(round(target_battery), 80)
            target_battery         = max(target_battery, round(battery_at_station) + 1)
            print(f"[scoring] complete_trip: battery_at_station={round(battery_at_station,1)}%, target={target_battery}%")

        charge_time        = estimate_charge_time(car_model, battery_level, target_battery, best_power_kw, best_is_dc)
        best_possible_time = estimate_charge_time(car_model, battery_level, target_battery, car_max_dc_kw, True)

        # Criterion 2: Speed (25 pts)
        speed_score = min(25, 25 * (best_possible_time / charge_time)) if charge_time > 0 and best_possible_time > 0 else 0
        score += speed_score

        # Criterion 3: Price (20 pts)
        price_score = (20 if not best_is_dc else 10) if price_sensitivity == "low cost" else 20
        score += price_score

        # Criterion 4: Accessibility (15 pts)
        available        = station.get("available", False)
        needs_membership = station.get("needs_membership", False)
        if available and not needs_membership:    access_score = 15
        elif available and needs_membership:      access_score = 10
        else:                                     access_score = 5
        score += access_score

        # Criterion 5: Past behavior (10 pts)
        if is_station_liked(profile, station["id"]):
            score += 10

        # Criterion 6: Familiar location (5 pts)
        for dest in saved_dests:
            if not dest.get("lat") or not dest.get("lng"):
                continue
            if _haversine(station_lat, station_lng, dest["lat"], dest["lng"]) <= 2:
                score += 5
                break

        station["score"]           = round(score, 2)
        station["best_connector"]  = best_connector
        station["charge_time_min"] = round(charge_time)
        station["target_battery"]  = round(target_battery)
        station["_detour_score"]   = round(detour_score, 2)
        station["_speed_score"]    = round(speed_score, 2)
        station["_price_score"]    = round(price_score, 2)
        station["_access_score"]   = round(access_score, 2)

        scored.append(station)

    # ── STEP 3 — SORT ─────────────────────────────────────────────────────────
    CRITERION_KEY = {
        "detour": "_detour_score",
        "speed":  "_speed_score",
        "price":  "_price_score",
        "access": "_access_score",
    }

    active_pref = get_active_preference(profile)

    if active_pref and active_pref in CRITERION_KEY:
        print(f"[scoring] Sorting by preference: {active_pref}")
        ranked = sorted(scored, key=lambda x: x.get(CRITERION_KEY[active_pref], 0), reverse=True)
    else:
        print(f"[scoring] No dominant preference — sorting by total score")
        ranked = sorted(scored, key=lambda x: x["score"], reverse=True)

    for s in ranked:
        for k in ["_detour_score", "_speed_score", "_price_score", "_access_score"]:
            s.pop(k, None)

    return ranked