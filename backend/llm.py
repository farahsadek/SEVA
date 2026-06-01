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

load_dotenv()

llm = ChatGroq(
    api_key=os.getenv("GROQ_API_KEY"),
    model="llama-3.1-8b-instant",
    streaming=False,
    max_tokens=150
)

_current_profile  = None
_last_map_data    = None
_last_trip_origin = None      # remembers last known origin across turns
_last_trip_dest   = None      # remembers last known destination across turns


def get_last_map_data():
    return _last_map_data


def clear_last_map_data() -> None:
    """Clear cached station and profile data when a new user logs in or logs out."""
    global _last_map_data, _current_profile, _last_trip_origin, _last_trip_dest
    _last_map_data    = None
    _current_profile  = None
    _last_trip_origin = None
    _last_trip_dest   = None
    print(f"[llm] Cleared cached profile and map data")


# ── Memory ────────────────────────────────────────────────────────────────────
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


# ── System Prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are SEVA, an intelligent Electric Vehicle charging assistant for Egyptian drivers.
You help EV drivers find the best charging station that is route-aware, not just the nearest.

You are a conversational assistant. You remember everything said in the conversation.

CRITICAL OUTPUT RULES:
- NEVER use HTML tags. No <div>, <span>, <p>, or any HTML.
- NEVER use markdown code blocks (no ``` or ~~~).
- Use ONLY plain text.

RESPONSE RULES:
- Answer the actual question being asked
- For general EV questions, answer conversationally
- No filler phrases like "Great question!"

WHEN TO CALL find_charging_stations:
- ONLY call it for a brand new trip where the user needs station recommendations AND there is NO existing [Context: Top 3 recommended stations] in this message yet.
- If [Context: Top 3 recommended stations] is already present, NEVER call the tool — answer entirely from that context.
- Signs it's a follow-up (DO NOT call tool): "option 1/2/3", "the first/second/third one", "how much", "how long", "is it fast", "membership", "which is", "what about", "tell me more", "how far is the detour", price or time questions about a current recommendation.
- Signs it's a new trip (call tool): user provides a new destination not discussed before.
- If no stations are found along the route, say so clearly and suggest trying a nearby major area.
- If battery context shows 30% or below, always call find_charging_stations — never tell the user they can complete a trip without verifying first.
- You ONLY have access to find_charging_stations. NEVER attempt to call brave_search, web_search, or any other tool. If a location is unrecognized, ask the user to clarify — do not search the web.

WHEN RECOMMENDING STATIONS:
The tool returns top 3 stations as structured data. Write a single neutral intro sentence only.
Do NOT list details — the UI displays the station cards automatically.
Example intro: "Here are the top 3 charging stations for your route from [origin] to [destination]."
Do NOT repeat operator, connectors, score, or any fields — they are shown in the cards.

CHARGING PRICES IN EGYPT:
- AC charging: 3.97 EGP per kWh
- DC fast charging: 7.67 EGP per kWh
To calculate cost: Energy (kWh) = (battery_kwh x percentage_to_charge / 100), then Cost = Energy x Rate.

LOCATION HANDLING:
- If the message contains [Context: GPS_ORIGIN: lat,lng], pass exactly "lat,lng" (just the number pair) as the origin parameter in find_charging_stations.
- If the user explicitly states an origin like "from Maadi" or "from GUC", use that place name as origin — NOT the GPS coordinates.
- If neither GPS nor explicit origin is available, ask: "Where are you starting from?"

AVOIDED STATIONS:
- If context contains AVOIDED STATIONS, never recommend any of those stations.

WHEN ANSWERING FOLLOW-UP QUESTIONS:
Use the station context [Context: Top 3 stations...] to answer precisely.
If user asks about "option 2" or "the second one", answer about that specific station.
If user asks a generic question like "which is fastest?", compare all 3.
If user asks "how much will I pay?", use the Rate field and charge time to calculate exact EGP.
Keep answers SHORT — maximum 2 sentences.
Never repeat information the user didn't ask for.
Never add unsolicited advice or extra context.

Examples of good short answers:
- "Is it a fast charger?" → "Yes, it has a 120kW DC charger."
- "How much will I pay?" → "At 7.67 EGP/kWh for DC, charging from 60% to 80% (18 kWh) costs approximately 138 EGP."
- "Do I need a membership?" → "Yes, this station requires a membership."
- "How far is the detour?" → "The detour is 0.56 km from your route."

Examples of bad long answers (never do this):
- Restating the station name and all its details before answering
- Adding "I hope this helps" or similar phrases
- Explaining what DC charging is when the user just asked yes/no
"""


# ── Location helpers ──────────────────────────────────────────────────────────
def _looks_like_trip(msg: str) -> bool:
    """Check if the message is a trip request with a destination."""
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
    """Check if user mentioned a specific named origin (not a generic phrase)."""
    lower = msg.lower()
    # Look for standalone word "from" followed by a place name
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
            content = msg.content

            # Strip ALL context blocks including multi-line avoided stations
            lines       = content.split("\n")
            clean_lines = []
            in_context  = False

            for line in lines:
                stripped = line.strip()
                if stripped.startswith("[Context:"):
                    in_context = True
                    # Single-line context block ends on same line
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


# ── RAG: format top 3 for LLM ────────────────────────────────────────────────
def format_stations_for_llm(stations: list, charging_mode: str = "charge_to_80") -> str:
    if not stations:
        return "No charging stations found along this route."

    count        = min(len(stations), 3)
    count_word   = {1: "one", 2: "two", 3: "three"}.get(count, str(count))
    target_label = "100%" if charging_mode == "charge_to_100" else "80%"
    lines        = [f"TOP {count} CHARGING STATION{'S' if count > 1 else ''} (ranked best first, real data only):"]

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

    lines.append(
        f"\nWrite ONE neutral intro sentence only. "
        f"There are exactly {count_word} station{'s' if count > 1 else ''} — say 'top {count_word}' not 'top 3' unless there are 3. "
        f"Do NOT list any station details — the UI renders cards automatically."
        f"\nEnd your response with exactly: [SHOW_CARDS]"
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
    Find real EV charging stations along a driving route in Egypt.
    Only call this when the user is asking for charging station recommendations
    for a specific trip with an origin and destination.
    Do NOT call this for follow-up questions about stations already mentioned.

    Args:
        origin: Starting location as a string or lat,lng coordinates (e.g. "Maadi" or "30.01,31.45")
        destination: End location as a string (e.g. "Cairo Airport")
        battery_level: Current battery as an integer between 1 and 100. Default is 100.
    """
    global _current_profile, _last_map_data, _charging_mode, _last_trip_origin, _last_trip_dest

    # ── Store trip context for follow-up memory ──
    _last_trip_origin = origin
    _last_trip_dest   = destination

    # ── Battery level ──
    if battery_level is not None:
        try:
            battery_level = int(battery_level)
        except (ValueError, TypeError):
            battery_level = None

    if battery_level is None:
        battery_level = _current_profile.get("battery_pct", 100) if _current_profile else 100

    print(f"[llm] Battery level used: {battery_level}%")
    print(f"[llm] Origin received by tool: {origin}")

    # ── Fetch route and stations ──
    stations, route = get_stations_along_route(origin, destination)

    # ── Error Case 1: Location not recognized ──
    if not route and not stations:
        # Try to give specific feedback about which location failed
        from .geocoding import geocode as _geocode
        origin_ok = _geocode(origin) is not None
        dest_ok   = _geocode(destination) is not None
        if not origin_ok and not dest_ok:
            return (
                f"I couldn't recognize either '{origin}' or '{destination}' as a location in Egypt. "
                f"Try using a well-known landmark, neighborhood, or compound name — "
                f"for example 'Maadi', 'New Cairo', 'Cairo Festival City', or 'GUC'."
            )
        elif not origin_ok:
            return (
                f"I couldn't find '{origin}' on the map. "
                f"Try a nearby landmark or neighborhood — for example 'Maadi' or 'Katameya'."
            )
        elif not dest_ok:
            return (
                f"I couldn't find '{destination}' on the map. "
                f"Try a nearby landmark or neighborhood instead."
            )
        else:
            return (
                f"I couldn't calculate a driving route from '{origin}' to '{destination}'. "
                f"Try using more specific location names."
            )

    # ── Error Case 2: Route found but no stations ──
    if not route:
        return (
            f"I found '{origin}' on the map but couldn't calculate a route to '{destination}'. "
            f"Please check the destination name and try again."
        )

    print(f"[llm] Found {len(stations)} stations along route")

    # ── Error Case 3: No stations along this route ──
    if not stations:
        return (
            f"No charging stations were found along the route from {origin} to {destination}. "
            f"Egypt's charging network is still growing — try a route through New Cairo, "
            f"Maadi, or Sheikh Zayed where stations are more concentrated."
        )

    # ── Score and rank ──
    if stations and route and _current_profile:
        try:
            stations = score_and_rank_stations(
                stations, _current_profile, route, battery_level, _charging_mode
            )
            print(f"[llm] Stations scored and ranked successfully")
            print(f"[llm] Top station score: {stations[0].get('score')}")

        except ValueError as e:
            error_msg = str(e)
            car_model = _current_profile.get("car_model", "your car")
            min_threshold = _current_profile.get("min_battery_threshold", 20)

            # ── Error Case 4: Already sufficient battery ──
            if "already at 80%" in error_msg or "already full" in error_msg:
                return (
                    f"Your battery is already at {battery_level}% — "
                    f"no charging stop needed for this trip."
                )

            # ── Error Case 5: Battery sufficient to complete trip ──
            elif "enough to complete" in error_msg or "No charging needed" in error_msg:
                return (
                    f"Good news — your {battery_level}% battery is enough to complete "
                    f"this trip without stopping to charge. Have a safe drive!"
                )

            # ── Error Case 6: Battery too low to reach any station ──
            elif "too low" in error_msg or "usable charge" in error_msg:
                usable = max(0, battery_level - min_threshold)
                return (
                    f"Your battery is too low to safely reach any charging station. "
                    f"At {battery_level}% with your {min_threshold}% minimum threshold, "
                    f"you only have {usable}% of usable charge remaining. "
                    f"Please charge to at least {min_threshold + 20}% before attempting this trip."
                )

            # ── Error Case 7: No compatible connectors ──
            elif "compatible" in error_msg or "connector" in error_msg:
                return (
                    f"None of the stations along this route have connectors compatible "
                    f"with your {car_model}. "
                    f"Try a different route or check your connector type in your profile settings."
                )

            # ── Error Case 8: All stations avoided ──
            elif "avoided" in error_msg:
                return (
                    f"All charging stations along this route are in your avoided list. "
                    f"You can remove stations from your avoided list by tapping the feedback "
                    f"button on a previous recommendation, or try a different route."
                )

            # ── Error Case 9: Car model not in database ──
            elif "not found in EV database" in error_msg:
                return (
                    f"I don't have specifications for '{car_model}' in my database. "
                    f"Please update your car model in your profile settings."
                )

            # ── Error Case 10: Detour too tight (no stations within limit) ──
            elif "detour" in error_msg.lower():
                max_detour = _current_profile.get("max_detour_km", 5)
                return (
                    f"No charging stations were found within your {max_detour}km detour limit. "
                    f"You can increase your maximum detour distance in your profile settings, "
                    f"or try a different route."
                )

            # ── Error Case 11: Mixed rejections ──
            else:
                return (
                    f"No suitable charging stations were found for this trip. "
                    f"This could be due to your connector type, battery level, or detour settings. "
                    f"Try adjusting your profile preferences or choosing a different route."
                )

        except Exception as e:
            print(f"[llm] Scoring error, falling back to unranked: {e}")

    # ── Store top 3 in _last_map_data ──
    if stations and route:
        global _last_map_data
        top3           = stations[:3]
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
                "origin_lat":         route["origin_coords"][0],
                "origin_lng":         route["origin_coords"][1],
                "dest_lat":           route["dest_coords"][0],
                "dest_lng":           route["dest_coords"][1],
                "origin_name":        origin,
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

    station_text = format_stations_for_llm(stations, _charging_mode)
    print(f"[llm] Formatted station info for LLM:\n{station_text}")

    if route:
        return (
            station_text +
            f"\nROUTE: {route['distance_km']} km, ~{int(route['duration_min'])} min drive."
        )
    return station_text


# ── Agent ─────────────────────────────────────────────────────────────────────
tools = [find_charging_stations]

agent = create_react_agent(
    model=llm,
    tools=tools,
    prompt=SYSTEM_PROMPT,
    checkpointer=checkpointer
)


# ── Main Chat Function ────────────────────────────────────────────────────────
def get_seva_response(user_message: str, session_id: str = "default", profile: dict = None) -> str:
    global _current_profile
    _current_profile = profile if profile else get_default_profile()

    battery          = _current_profile.get("battery_pct", 100)
    # Strip % sign if passed as string from frontend
    if isinstance(battery, str):
        battery = int(battery.replace("%", "").strip())
    current_location = _current_profile.get("current_location")

    battery_context = f"[Context: Current battery level is {battery}%]\n"

    # ── Fix Issue 1: Inject reachability warning if battery is critically low ──
    reachability_context = ""
    if battery <= 30:
        reachability_context = (
            f"[Context: Battery is {battery}%. Call find_charging_stations now.]\n"
        )
    # ── Fix Issue 2: Inject last known trip context for follow-up questions ──
    trip_context = ""
    if _last_trip_origin and _last_trip_dest and not _looks_like_trip(user_message):
        trip_context = (
            f"[Context: The user's current trip is from '{_last_trip_origin}' "
            f"to '{_last_trip_dest}'. Use this if they ask follow-up questions "
            f"about their trip without restating origin/destination.]\n"
        )

    # location_context is set later in the injection block, only when GPS is actually used
    location_context = ""

    # ── RAG: inject all 3 station details for follow-up answers ──
    station_context = ""
    if _last_map_data and isinstance(_last_map_data, list) and len(_last_map_data) > 0:
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

    # ── RAG: inject avoided stations ──
    avoided_context = ""
    if _current_profile:
        avoided_str = get_avoided_station_context(_current_profile)
        if avoided_str:
            avoided_context = f"[Context: {avoided_str}]\n"

    # ── Smart location injection ──
    processed_message = user_message
    gps_injected      = False

    if current_location:
        lower  = user_message.lower()
        coords = f"{current_location['lat']},{current_location['lng']}"

        if "my current location" in lower or "from here" in lower:
            # User explicitly referenced their location — replace the phrase with coords
            processed_message = user_message.replace("my current location", coords).replace("from here", coords)
            gps_injected      = True
            print(f"[llm] Replaced location phrase with GPS coords")

        elif _looks_like_trip(user_message) and not _has_explicit_origin(user_message):
            # Append GPS as a context block so it's stripped from chat history display
            processed_message = f"{user_message}\n[Context: GPS_ORIGIN: {coords}]"
            gps_injected      = True
            print(f"[llm] No origin detected — appended GPS_ORIGIN tag")

    # Only inject location_context when GPS is actually being used as origin
    if current_location and gps_injected:
        location_context = (
            f"[Context: GPS origin available: {current_location['lat']},{current_location['lng']}"
            " — pass this as the origin to find_charging_stations.]\n"
        )
    else:
        location_context = ""

    augmented_message = battery_context + reachability_context + trip_context + location_context + station_context + avoided_context + processed_message

    config = {"configurable": {"thread_id": session_id}}

    print(f"[llm] Session ID: {session_id}")
    print(f"[llm] Station context injected: {bool(station_context)}")
    print(f"[llm] Avoided context injected: {bool(avoided_context)}")

    # ── Trim history if too long ──
    try:
        checkpoint = checkpointer.get(config)
        if checkpoint:
            existing_messages = checkpoint["channel_values"].get("messages", [])
            if len(existing_messages) > 14:
                print(f"[llm] Trimming history: {len(existing_messages)} → clearing")
                db = _mongo_client["seva"]
                db["checkpoints"].delete_many({"thread_id": session_id})
                db["checkpoint_writes"].delete_many({"thread_id": session_id})
    except Exception as e:
        print(f"[llm] Trim check failed: {e}")

    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content=augmented_message)]},
            config=config
        )

        for msg in reversed(result["messages"]):
            if isinstance(msg, AIMessage) and msg.content:
                return msg.content

        return "I couldn't generate a response. Please try again."

    except Exception as e:
        error_str = str(e)
        print(f"[llm] Agent error: {e}")

        if "413" in error_str or "too large" in error_str.lower():
            return "Our conversation has gotten quite long. Please start a new trip and I'll help you from here."
        elif "rate_limit" in error_str.lower() or "429" in error_str:
            return "I'm a bit overloaded right now. Please wait a few seconds and try again."
        elif "timeout" in error_str.lower():
            return "That took too long to process. Please try again — this sometimes happens with complex routes."
        elif "brave_search" in error_str or "tool_use_failed" in error_str or "tool call validation" in error_str:
            return (
                "I couldn't find that location. Try using a well-known Cairo landmark or neighborhood — "
                "for example 'Maadi', 'New Cairo', 'Heliopolis', or 'Sheikh Zayed'."
            )
        elif "tool" in error_str.lower() and "not in request" in error_str.lower():
            return (
                "I couldn't recognize that location. Please use a specific place name in Egypt "
                "and I'll find charging stations along your route."
            )
        else:
            return "Something went wrong. Please try again or rephrase your request."