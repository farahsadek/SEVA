"""
llm.py — SEVA Recommendation Engine
Sole responsibility: call find_charging_stations, score results, cache them.
All personality, conversation, and followup handling lives in router.py.
"""

import os
import re
from langchain_groq import ChatGroq
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.mongodb.saver import MongoDBSaver
from dotenv import load_dotenv
from pymongo import MongoClient
from .stations import get_stations_along_route
from .scoring import score_and_rank_stations
from .profile import get_default_profile, get_avoided_station_context
from .routing import get_route

load_dotenv()

# ── LLM — tool-use model only, recommendation agent ──────────────────────────
llm = ChatGroq(
    api_key=os.getenv("GROQ_API_KEY"),
    model="llama-3.3-70b-versatile",
    streaming=False,
    max_tokens=150
)

# ── Global cache ──────────────────────────────────────────────────────────────
_current_profile    = None
_last_map_data      = None
_last_trip_origin   = None
_last_trip_dest     = None
_last_battery_level = None
_last_station_pool  = None


# ── Cache accessors ───────────────────────────────────────────────────────────
def get_last_map_data():
    return _last_map_data

def get_last_trip_context() -> dict | None:
    if _last_trip_origin and _last_trip_dest:
        return {
            "origin":        _last_trip_origin,
            "destination":   _last_trip_dest,
            "battery_level": _last_battery_level,
        }
    return None

def get_raw_station_pool() -> list | None:
    return _last_station_pool

def clear_last_map_data() -> None:
    global _last_map_data, _current_profile, _last_trip_origin, _last_trip_dest, _last_battery_level, _last_station_pool
    _last_map_data      = None
    _current_profile    = None
    _last_trip_origin   = None
    _last_trip_dest     = None
    _last_battery_level = None
    _last_station_pool  = None
    print("[llm] Cleared all cached data")

def clear_map_data_only() -> None:
    global _last_map_data
    _last_map_data = None
    # Intentionally preserve _last_station_pool, _last_trip_origin/dest, _last_battery_level
    # so refine and followup still work after a new recommendation clears the cards


# ── MongoDB memory ────────────────────────────────────────────────────────────
_mongo_client = MongoClient(os.getenv("MONGODB_URI"))

checkpointer = MongoDBSaver(
    client=_mongo_client,
    db_name="seva",
    checkpoint_collection_name="checkpoints",
    writes_collection_name="checkpoint_writes"
)

def clear_memory(session_id: str) -> None:
    db = _mongo_client["seva"]
    db["checkpoints"].delete_many({"thread_id": session_id})
    db["checkpoint_writes"].delete_many({"thread_id": session_id})
    print(f"[llm] Memory cleared for session: {session_id}")


# ── System Prompt — recommender only, no personality ─────────────────────────
SYSTEM_PROMPT = """You are a tool-calling agent for SEVA, an EV charging assistant in Egypt.
Your ONLY job is to call find_charging_stations with the correct origin, destination, and battery_level.
You do NOT answer general questions. You do NOT answer follow-up questions. You only find charging stations.
CRITICAL: Call find_charging_stations ONLY ONCE per user message. 
Never call it twice. If you already have station results, write the intro sentence immediately.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHEN TO CALL find_charging_stations
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Call it when the user needs charging station recommendations for a specific trip.
- NEVER call it for follow-up questions about stations already shown.
- NEVER call brave_search, web_search, or any other tool — you only have find_charging_stations.
- If battery context shows 30% or below AND a destination is mentioned → always call the tool immediately.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOCATION HANDLING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- If the message contains [Context: GPS_ORIGIN: lat,lng] → pass exactly "lat,lng" as origin.
- If the user says "from Maadi" or "from GUC" → use that place name as origin exactly.
- If neither GPS nor explicit origin is in the message → ask: "Where are you starting from?"
- If a location name is unrecognized → ask the user to clarify. Never guess or invent locations.
- Egypt-specific: common origins include Maadi, New Cairo, Nasr City, Heliopolis, Zamalek,
  6th of October, Sheikh Zayed, Tagamoa, Rehab, Katameya, GUC, AUC, Cairo Festival City.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BATTERY HANDLING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Extract battery level as an integer (1–100) from the message or context.
- Use [Context: Current battery level is X%] if the user didn't state it explicitly.
- Never pass a string like "30%" — always pass the integer 30.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- NEVER use HTML tags in your response.
- NEVER include GPS coordinates, lat/lng numbers, or any raw coordinate values.
- Say "your current location" when the origin was GPS coordinates.
- After the tool returns results, write ONE neutral intro sentence only.
  Example: "Here are the top 3 charging stations for your route from Maadi to New Cairo."
- Do NOT list station details — the UI renders cards automatically.
- End your response with exactly: [SHOW_CARDS]
- Do NOT add advice, comparisons, or extra commentary after [SHOW_CARDS].
"""


# ── Location helpers ──────────────────────────────────────────────────────────
def _looks_like_trip(msg: str) -> bool:
    lower = msg.lower()
    has_movement = any(p in lower for p in [
        "going to", "heading to", "driving to", "driving from",
        "travelling to", "traveling to", "i want to go",
        "i am going", "i'm going", "i need to get to",
        "destination", "route",
    ])
    has_battery = any(p in lower for p in ["battery", "%", "charge"])
    return has_movement and has_battery


def _has_explicit_origin(msg: str) -> bool:
    lower = msg.lower()
    match = re.search(r'\bfrom\b\s+(\S+)', lower)
    if match:
        after_from = match.group(1).strip()
        generic = ["here", "my", "current", "this"]
        if not any(after_from.startswith(g) for g in generic):
            return True
    return False


# ── Chat History ──────────────────────────────────────────────────────────────
def get_chat_history(session_id: str) -> list:
    checkpoint = checkpointer.get({"configurable": {"thread_id": session_id}})
    if not checkpoint:
        return []

    messages = checkpoint["channel_values"].get("messages", [])
    history  = []

    for msg in messages:
        if isinstance(msg, HumanMessage) and msg.content:
            content     = msg.content
            lines       = content.split("\n")
            clean_lines = []
            in_context  = False

            for line in lines:
                stripped = line.strip()
                if stripped.startswith("[Context:"):
                    in_context = True
                    if stripped.endswith("]") and stripped.count("[") == stripped.count("]"):
                        in_context = False
                    continue
                if in_context:
                    if stripped.endswith("]"):
                        in_context = False
                    continue
                clean_lines.append(line)

            content = "\n".join(clean_lines).strip()
            if content:
                history.append({"role": "user", "content": content})

        elif isinstance(msg, AIMessage) and msg.content:
            history.append({"role": "assistant", "content": msg.content})

    return history


# ── Format stations for LLM response ─────────────────────────────────────────
def format_stations_for_llm(stations: list, charging_mode: str = "charge_to_80", destination: str = "") -> str:
    if not stations:
        return "No charging stations found along this route."

    count        = min(len(stations), 3)
    count_word   = {1: "one", 2: "two", 3: "three"}.get(count, str(count))
    target_label = "100%" if charging_mode == "charge_to_100" else "80%"
    lines        = [f"TOP {count} CHARGING STATION{'S' if count > 1 else ''} (ranked best first):"]

    for i, s in enumerate(stations[:3]):
        connector_details = ", ".join(
            f"{c['connection_type_title']} ({c['power_kw']}kW)"
            for c in s.get("connectors", [])
            if c["connection_type_title"] != "Unknown"
        ) or "Unknown"

        status = "Available" if s["available"] else "Limited"
        access = "Members only" if s["needs_membership"] else "Public"

        lines.append(
            f"Option {i+1}: {s['name']} | "
            f"Operator: {s.get('operator', 'Unknown')} | "
            f"Connectors: {connector_details} | "
            f"Fast: {'Yes' if s['has_fast_charge'] else 'No'} | "
            f"Time: ~{s.get('charge_time_min', '?')} min to {target_label} | "
            f"{status} | {access} | "
            f"Score: {s.get('score', '?')}/100"
        )

    dest_str = f" to {destination}" if destination else ""
    lines.append(
        f"\nWrite ONE neutral intro sentence: "
        f"'Here are the top {count_word} charging station{'s' if count > 1 else ''} for your route{dest_str}.'"
        f" Say 'your current location' if origin was GPS. "
        f"Do NOT list details. End with exactly: [SHOW_CARDS]"
    )
    return "\n".join(lines)


# ── Charging mode ─────────────────────────────────────────────────────────────
_charging_mode = "charge_to_80"

def set_charging_mode(mode: str) -> None:
    global _charging_mode
    _charging_mode = mode


# ── Tool ──────────────────────────────────────────────────────────────────────
@tool
def find_charging_stations(origin: str, destination: str, battery_level: int = 100) -> str:
    """
    Find and rank EV charging stations along a driving route in Egypt.
    Call this ONLY for trip-based station recommendations.
    Do NOT call for follow-up questions about previously shown stations.

    Args:
        origin:        Starting location — place name (e.g. "Maadi") or "lat,lng" coordinates.
        destination:   End location — place name (e.g. "Cairo Airport").
        battery_level: Current battery percentage as integer (1–100). Default 100.
    """
    global _current_profile, _last_map_data, _charging_mode, _last_trip_origin, _last_trip_dest, _last_battery_level

    # ── Cache trip context ──
    _last_trip_origin = origin
    _last_trip_dest   = destination

    # ── Normalize battery level ──
    if battery_level is not None:
        try:
            battery_level       = int(battery_level)
            _last_battery_level = battery_level
        except (ValueError, TypeError):
            battery_level = None

    if battery_level is None:
        battery_level = _current_profile.get("battery_pct", 100) if _current_profile else 100

    print(f"[llm] Battery: {battery_level}% | Origin: {origin} | Destination: {destination}")

    # ── Fetch route and stations ──
    stations, route = get_stations_along_route(origin, destination)

    # ── Error: location not recognized ──
    if not route and not stations:
        from .geocoding import geocode as _geocode
        origin_ok = _geocode(origin) is not None
        dest_ok   = _geocode(destination) is not None
        if not origin_ok and not dest_ok:
            return (
                f"I couldn't recognize '{origin}' or '{destination}' as locations in Egypt. "
                f"Try a well-known landmark or neighborhood like 'Maadi', 'New Cairo', or 'GUC'."
            )
        elif not origin_ok:
            return f"I couldn't find '{origin}' on the map. Try a nearby landmark like 'Maadi' or 'Katameya'."
        elif not dest_ok:
            return f"I couldn't find '{destination}' on the map. Try a nearby landmark or neighborhood."
        else:
            return f"I couldn't calculate a route from '{origin}' to '{destination}'. Try more specific names."

    if not route:
        return f"I found '{origin}' but couldn't calculate a route to '{destination}'. Please check the destination name."

    if not stations:
        return (
            f"No charging stations found along the route from {origin} to {destination}. "
            f"Egypt's network is growing — try routes through New Cairo, Maadi, or Sheikh Zayed."
        )

    print(f"[llm] Found {len(stations)} stations along route")

    # ── Score and rank ──
    if stations and route and _current_profile:
        try:
            stations = score_and_rank_stations(
                stations, _current_profile, route, battery_level, _charging_mode
            )
            print(f"[llm] Scoring complete. Top score: {stations[0].get('score')}")

        except ValueError as e:
            error_msg     = str(e)
            car_model     = _current_profile.get("car_model", "your car")
            min_threshold = _current_profile.get("min_battery_threshold", 20)

            if "already at 80%" in error_msg or "already full" in error_msg:
                return f"Your battery is already at {battery_level}% — no charging needed for this trip."

            elif "enough to complete" in error_msg or "No charging needed" in error_msg:
                return f"Your {battery_level}% battery is enough to complete this trip without charging. Safe drive!"

            elif "too low" in error_msg or "usable charge" in error_msg:
                usable = max(0, battery_level - min_threshold)
                return (
                    f"Your battery is too low to reach any station safely. "
                    f"At {battery_level}% with a {min_threshold}% minimum, you only have {usable}% usable. "
                    f"Please charge to at least {min_threshold + 20}% first."
                )

            elif "compatible" in error_msg or "connector" in error_msg:
                return (
                    f"No stations along this route are compatible with your {car_model}. "
                    f"Try a different route or check your connector type in profile settings."
                )

            elif "avoided" in error_msg:
                return (
                    f"All stations along this route are in your avoided list. "
                    f"Remove some avoided stations or try a different route."
                )

            elif "not found in EV database" in error_msg:
                return f"I don't have specs for '{car_model}'. Please update your car model in profile settings."

            elif "detour" in error_msg.lower():
                max_detour = _current_profile.get("max_detour_km", 5)
                return (
                    f"No stations within your {max_detour}km detour limit. "
                    f"Increase your max detour in profile settings or try a different route."
                )

            else:
                return (
                    f"No suitable stations found — could be connector type, battery level, or detour settings. "
                    f"Try adjusting your profile preferences or choosing a different route."
                )

        except Exception as e:
            print(f"[llm] Scoring error, using unranked results: {e}")

    # ── Cache top 3 and full pool ──
    if stations and route:
        global _last_map_data, _last_station_pool
        _last_station_pool = stations
        top3               = stations[:3]
        _last_map_data     = []

        for s in top3:
            connector_details = ", ".join(
                f"{c['connection_type_title']} ({c['power_kw']}kW)"
                for c in s.get("connectors", [])
                if c["connection_type_title"] != "Unknown"
            ) or "Unknown"

            is_dc = s["has_fast_charge"]
            rate  = 7.67 if is_dc else 3.97

            _last_map_data.append({
                "station_id":         str(s["id"]),
                "station_name":       s["name"],
                "station_lat":        s["lat"],
                "station_lng":        s["lng"],
                "origin_lat":         route["origin_coords"][0],
                "origin_lng":         route["origin_coords"][1],
                "dest_lat":           route["dest_coords"][0],
                "dest_lng":           route["dest_coords"][1],
                "origin_name":        "Your current location" if origin and origin[0].isdigit() else origin,
                "dest_name":          destination,
                "operator":           s.get("operator", "Unknown"),
                "connectors":         connector_details,
                "has_fast_charge":    s["has_fast_charge"],
                "charge_time":        s.get("charge_time_min", "?"),
                "target_battery":     s.get("target_battery", 80),
                "score":              s.get("score", "?"),
                "available":          s["available"],
                "needs_membership":   s["needs_membership"],
                "route_distance_km":  route["distance_km"],
                "route_duration_min": route["duration_min"],
                "rate_egp_kwh":       rate,
                "detour_km":          s.get("detour_km", 0),
            })

    station_text = format_stations_for_llm(stations, _charging_mode, destination)
    print(f"[llm] Station text prepared for agent")

    if route:
        return station_text + f"\nROUTE: {route['distance_km']} km, ~{int(route['duration_min'])} min."
    return station_text


# ── Agent ─────────────────────────────────────────────────────────────────────
tools = [find_charging_stations]

agent = create_react_agent(
    model=llm,
    tools=tools,
    prompt=SYSTEM_PROMPT,
    checkpointer=checkpointer
)


# ── Recommendation agent entry point ─────────────────────────────────────────
def run_recommendation_agent(user_message: str, session_id: str = "default", profile: dict = None) -> str:
    global _current_profile
    _current_profile = profile if profile else get_default_profile()

    battery = _current_profile.get("battery_pct", 100)
    if isinstance(battery, str):
        battery = int(battery.replace("%", "").strip())

    current_location = _current_profile.get("current_location")

    # ── Context injections ──
    battery_context      = f"[Context: Current battery level is {battery}%]\n"
    reachability_context = ""
    trip_context         = ""
    location_context     = ""
    station_context      = ""
    avoided_context      = ""

    if battery <= 30:
        reachability_context = f"[Context: Battery is critically low at {battery}%. Call find_charging_stations immediately.]\n"

    if _last_trip_origin and _last_trip_dest and not _looks_like_trip(user_message):
        trip_context = (
            f"[Context: Previous trip was from '{_last_trip_origin}' to '{_last_trip_dest}'. "
            f"Use this if the user is asking about this trip without restating origin/destination.]\n"
        )

    if _last_map_data and isinstance(_last_map_data, list):
        parts = []
        for i, s in enumerate(_last_map_data):
            parts.append(
                f"Option {i+1}: {s.get('station_name')} | "
                f"Operator: {s.get('operator')} | "
                f"Connectors: {s.get('connectors')} | "
                f"Fast charge: {'Yes' if s.get('has_fast_charge') else 'No'} | "
                f"Charge time: ~{s.get('charge_time')} min to {s.get('target_battery')}% | "
                f"Access: {'Members only' if s.get('needs_membership') else 'Public'} | "
                f"Score: {s.get('score')}/100 | "
                f"Detour: {s.get('detour_km', 0)} km | "
                f"Rate: {s.get('rate_egp_kwh')} EGP/kWh"
            )
        station_context = f"[Context: Top 3 recommended stations — {' || '.join(parts)}]\n"

    if _current_profile:
        avoided_str = get_avoided_station_context(_current_profile)
        if avoided_str:
            avoided_context = f"[Context: {avoided_str}]\n"

    # ── GPS injection ──
    processed_message = user_message
    gps_injected      = False

    if current_location:
        lower  = user_message.lower()
        coords = f"{current_location['lat']},{current_location['lng']}"

        if "my current location" in lower or "from here" in lower:
            processed_message = user_message.replace("my current location", coords).replace("from here", coords)
            gps_injected      = True
            print("[llm] Replaced location phrase with GPS coords")

        elif _looks_like_trip(user_message) and not _has_explicit_origin(user_message):
            processed_message = f"{user_message}\n[Context: GPS_ORIGIN: {coords}]"
            gps_injected      = True
            print("[llm] Appended GPS_ORIGIN tag")

    if current_location and gps_injected:
        location_context = (
            f"[Context: GPS origin: {current_location['lat']},{current_location['lng']}"
            " — pass as origin to find_charging_stations.]\n"
        )

    augmented_message = (
        battery_context + reachability_context + trip_context +
        location_context + station_context + avoided_context +
        processed_message
    )

    config = {"configurable": {"thread_id": session_id}}
    print(f"[llm] Session: {session_id} | Station context: {bool(station_context)} | Avoided: {bool(avoided_context)}")

    # ── Trim history if too long ──
    try:
        checkpoint = checkpointer.get(config)
        if checkpoint:
            msgs = checkpoint["channel_values"].get("messages", [])
            if len(msgs) > 14:
                print(f"[llm] History too long ({len(msgs)} msgs) — clearing")
                db = _mongo_client["seva"]
                db["checkpoints"].delete_many({"thread_id": session_id})
                db["checkpoint_writes"].delete_many({"thread_id": session_id})
    except Exception as e:
        print(f"[llm] Trim check failed: {e}")

    # ── Invoke agent ──
    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content=augmented_message)]},
            config=config
        )
        for msg in reversed(result["messages"]):
            if isinstance(msg, AIMessage) and msg.content:
                response = msg.content
                # If agent wrote the intro but tool actually returned an error, surface the error
                if "[SHOW_CARDS]" in response and not _last_map_data:
                    # Tool ran but produced no stations — return last tool error instead
                    for m in reversed(result["messages"]):
                        if hasattr(m, "content") and isinstance(m.content, str):
                            if any(err in m.content for err in [
                                "battery is already", "enough to complete", "too low",
                                "compatible", "avoided", "not found", "detour", "No suitable",
                                "No charging stations", "couldn't recognize", "couldn't find",
                                "couldn't calculate"
                            ]):
                                return m.content
                return response
    
    except Exception as e:
        error_str = str(e)
        print(f"[llm] Agent error: {e}")

        if "413" in error_str or "too large" in error_str.lower():
            return "Our conversation is too long. Please start a new trip."
        elif "rate_limit" in error_str.lower() or "429" in error_str:
            return "I'm overloaded right now. Please wait a few seconds and try again."
        elif "timeout" in error_str.lower():
            return "That took too long. Please try again — this sometimes happens with complex routes."
        elif "brave_search" in error_str or "tool_use_failed" in error_str or "tool call validation" in error_str:
            return "I couldn't find that location. Try a well-known Cairo landmark like 'Maadi' or 'New Cairo'."
        elif "tool" in error_str.lower() and "not in request" in error_str.lower():
            return "I couldn't recognize that location. Please use a specific place name in Egypt."
        else:
            return "Something went wrong. Please try again or rephrase your request."


# ── Refine — re-rank cached pool without new API call ────────────────────────
def handle_refine(user_message: str, modification: str | None, session_id: str = "default", profile: dict = None) -> str:
    global _current_profile, _last_map_data

    if profile:
        _current_profile = profile

    # No cached pool → fall back to full recommendation
    if not _last_station_pool or not _last_trip_origin or not _last_trip_dest:
        print("[llm] handle_refine: no cached pool — falling back to agent")
        return run_recommendation_agent(user_message, session_id, profile)

    battery     = _last_battery_level or (_current_profile.get("battery_pct", 100) if _current_profile else 100)
    destination = _last_trip_dest

    # ── Battery override ──
    if modification and "battery_level=" in modification:
        try:
            battery = int(modification.split("battery_level=")[1].split()[0])
            print(f"[llm] handle_refine: battery → {battery}%")
        except Exception:
            pass

    # ── Destination override → needs new route → full agent ──
    if modification and "destination=" in modification:
        new_dest    = modification.split("destination=")[1].strip()
        destination = new_dest
        print(f"[llm] handle_refine: destination → '{destination}'")
        return run_recommendation_agent(
            f"I want to go from {_last_trip_origin} to {destination}, battery at {battery}%",
            session_id, profile
        )

    # ── Origin override → needs new route → full agent ──
    if modification and "origin=" in modification.lower():
        new_origin = modification.split("origin=")[1].strip()
        print(f"[llm] handle_refine: origin → '{new_origin}'")
        return run_recommendation_agent(
            f"I want to go from {new_origin} to {destination}, battery at {battery}%",
            session_id, profile
        )

    pool = _last_station_pool.copy()

    # ── Exclusion filter ──
    if modification and "exclude option" in modification.lower():
        try:
            idx = int(''.join(filter(str.isdigit, modification))) - 1
            if _last_map_data and 0 <= idx < len(_last_map_data):
                excluded_id = _last_map_data[idx]["station_id"]
                pool = [s for s in pool if str(s["id"]) != str(excluded_id)]
                print(f"[llm] handle_refine: excluded station {excluded_id}")
        except Exception:
            pass

    # ── DC only filter ──
    if modification and "dc_fast_only" in modification.lower():
        dc_only = [s for s in pool if s.get("has_fast_charge")]
        if dc_only:
            pool = dc_only
            print(f"[llm] handle_refine: DC-only filter → {len(pool)} stations")

    # ── Re-sort by modification ──
    if modification and "closer" in modification.lower():
        pool = sorted(pool, key=lambda s: s.get("detour_km", 999))
        print("[llm] handle_refine: sorted by detour (closer first)")

    if modification and "cheaper" in modification.lower():
        pool = sorted(pool, key=lambda s: 0 if not s.get("has_fast_charge") else 1)
        print("[llm] handle_refine: sorted by price (cheaper first)")

    if modification and "faster" in modification.lower():
        pool = sorted(pool, key=lambda s: s.get("charge_time_min", 999))
        print("[llm] handle_refine: sorted by charge time (fastest first)")

    if not pool:
        return "No stations match your refined criteria. Try adjusting your preferences or requesting a new route."

    # ── Rebuild _last_map_data with new top 3 ──
    route_obj = get_route(_last_trip_origin, destination)
    if not route_obj:
        return "I couldn't recalculate the route. Please try rephrasing your request."

    top3           = pool[:3]
    _last_map_data = []

    for s in top3:
        connector_details = ", ".join(
            f"{c['connection_type_title']} ({c['power_kw']}kW)"
            for c in s.get("connectors", [])
            if c["connection_type_title"] != "Unknown"
        ) or "Unknown"
        is_dc = s["has_fast_charge"]
        rate  = 7.67 if is_dc else 3.97
        _last_map_data.append({
            "station_id":         str(s["id"]),
            "station_name":       s["name"],
            "station_lat":        s["lat"],
            "station_lng":        s["lng"],
            "origin_lat":         route_obj["origin_coords"][0],
            "origin_lng":         route_obj["origin_coords"][1],
            "dest_lat":           route_obj["dest_coords"][0],
            "dest_lng":           route_obj["dest_coords"][1],
            "origin_name":        _last_trip_origin,
            "dest_name":          destination,
            "operator":           s.get("operator", "Unknown"),
            "connectors":         connector_details,
            "has_fast_charge":    s["has_fast_charge"],
            "charge_time":        s.get("charge_time_min", "?"),
            "target_battery":     s.get("target_battery", 80),
            "score":              s.get("score", "?"),
            "available":          s["available"],
            "needs_membership":   s["needs_membership"],
            "route_distance_km":  route_obj["distance_km"],
            "route_duration_min": route_obj["duration_min"],
            "rate_egp_kwh":       rate,
            "detour_km":          s.get("detour_km", 0),
        })

    count      = len(top3)
    count_word = {1: "one", 2: "two", 3: "three"}.get(count, str(count))
    mod_desc   = f" ({modification})" if modification else ""
    return (
        f"Here are the top {count_word} refined charging station{'s' if count > 1 else ''} "
        f"for your route from {_last_trip_origin} to {destination}{mod_desc}. [SHOW_CARDS]"
    )