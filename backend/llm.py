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
    model="llama-3.3-70b-versatile",
    streaming=False,
    max_tokens=150
)

_current_profile = None
_last_map_data   = None


def get_last_map_data():
    return _last_map_data


def clear_last_map_data() -> None:
    """Clear cached station and profile data when a new user logs in or logs out."""
    global _last_map_data, _current_profile
    _last_map_data   = None
    _current_profile = None
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
- For follow-up questions about specific stations, use the station context provided
- For general EV questions, answer conversationally
- Use find_charging_stations when the user mentions a destination or asks where to charge, even if it follows a previous recommendation
- If no stations are found along the route, tell the user clearly and suggest they try a nearby major area or check back later
- No filler phrases like "Great question!"

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
- If GPS coordinates appear in the origin field, use them directly as the starting point.
- If no origin is provided and no coordinates are available, ask: "Where are you starting from?"

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

    target_label = "100%" if charging_mode == "charge_to_100" else "80%"
    lines        = ["TOP 3 CHARGING STATIONS (ranked best first, real data only):"]

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
        "\nWrite ONE neutral intro sentence only. "
        "Do NOT list any station details — the UI renders cards automatically."
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
    global _current_profile, _last_map_data, _charging_mode

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

    if not route:
        return (
            f"I couldn't find a route from '{origin}' to '{destination}'. "
            f"Please check the location names and try again with more specific names."
        )

    print(f"[llm] Found {len(stations)} stations along route")

    if not stations:
        return (
            f"No charging stations were found along the route from {origin} to {destination}. "
            f"This area may not have stations yet — try a nearby major area like New Cairo or Maadi."
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
            if "already at 80%" in error_msg or "No charging needed" in error_msg:
                return f"Your battery is at {battery_level}% — already sufficient for this trip. No charging stop needed!"
            elif "enough charge" in error_msg:
                return error_msg
            elif "connector incompatibility" in error_msg:
                return f"None of the stations along this route are compatible with your {_current_profile.get('car_model', 'car')}."
            elif "avoided" in error_msg:
                return "All nearby stations are in your avoided list. Try clearing some or expanding your detour tolerance."
            else:
                return f"No suitable charging stations found. {error_msg}"
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
    current_location = _current_profile.get("current_location")

    battery_context = f"[Context: Current battery level is {battery}%]\n"

    if current_location:
        location_context = (
            f"[Context: User's GPS location is lat={current_location['lat']}, "
            f"lng={current_location['lng']}. Coordinates have been injected into "
            f"the message where needed.]\n"
        )
    else:
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

    if current_location:
        lower  = user_message.lower()
        coords = f"{current_location['lat']},{current_location['lng']}"

        if "my current location" in lower or "from here" in lower:
            # User explicitly referenced their location — replace the phrase with coords
            processed_message = user_message.replace("my current location", coords).replace("from here", coords)
            print(f"[llm] Replaced location phrase with GPS coords")

        elif _looks_like_trip(user_message) and not _has_explicit_origin(user_message):
            # Trip request with no named origin — prepend GPS coords silently
            processed_message = f"from {coords} {user_message}"
            print(f"[llm] No origin detected — prepended GPS coords silently")

    augmented_message = battery_context + location_context + station_context + avoided_context + processed_message

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
            return "Our conversation has gotten quite long. Please repeat your last request and I'll help you from here."
        elif "rate_limit" in error_str.lower() or "429" in error_str:
            return "I'm receiving too many requests right now. Please wait a few seconds and try again."
        elif "timeout" in error_str.lower():
            return "The request took too long. Please try again — this sometimes happens with complex routes."
        else:
            return "Sorry, something went wrong on my end. Please try again."