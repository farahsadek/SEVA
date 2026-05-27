import os
from pymongo import MongoClient
from dotenv import load_dotenv
from enum import Enum
from datetime import datetime, timezone

load_dotenv()

class PriceSensitivity(str, Enum):
    LOW_COST  = "low cost"
    ANY_PRICE = "any price"

class ACUsage(str, Enum):
    ALWAYS    = "always"
    SOMETIMES = "sometimes"
    NEVER     = "never"

client = MongoClient(os.getenv("MONGODB_URI"))
db = client["seva"]
profiles_collection = db["profiles"]


def profile_exists(name: str, pin: str) -> bool:
    return profiles_collection.find_one({"_id": f"{name.lower()}_{pin}"}) is not None


def name_exists(name: str) -> bool:
    return profiles_collection.find_one(
        {"name": {"$regex": f"^{name}$", "$options": "i"}}
    ) is not None


def create_profile(
    name: str, pin: str, car_model: str,
    price_sensitivity: PriceSensitivity, ac_usage: ACUsage,
    min_battery_threshold: int, max_detour_km: int,
    saved_destinations: list = []
) -> dict:
    profile = {
        "_id":                   f"{name.lower()}_{pin}",
        "name":                  name,
        "pin":                   pin,
        "car_model":             car_model,
        "price_sensitivity":     price_sensitivity,
        "ac_usage":              ac_usage,
        "min_battery_threshold": min_battery_threshold,
        "max_detour_km":         max_detour_km,
        "saved_destinations":    saved_destinations,
        "liked_stations":        [],
        "avoided_stations":      [],
        # ── Preference signal system ──
        "preferences": {
            "detour": [],
            "speed":  [],
            "price":  [],
            "access": [],
        },
    }
    profiles_collection.insert_one(profile)
    print(f"[profile] Created profile for {name}")
    return {k: v for k, v in profile.items() if k != "_id"}


def load_profile(name: str, pin: str) -> dict | None:
    profile = profiles_collection.find_one({"_id": f"{name.lower()}_{pin}"})
    if profile:
        # Ensure preferences field exists for older profiles
        if "preferences" not in profile:
            profile["preferences"] = {"detour": [], "speed": [], "price": [], "access": []}
            profiles_collection.update_one(
                {"_id": f"{name.lower()}_{pin}"},
                {"$set": {"preferences": profile["preferences"]}}
            )
        print(f"[profile] Loaded profile for {name}")
        return {k: v for k, v in profile.items() if k != "_id"}
    print(f"[profile] No profile found for {name}")
    return None


def save_profile(profile: dict) -> None:
    profile_id = f"{profile['name'].lower()}_{profile['pin']}"
    profiles_collection.update_one({"_id": profile_id}, {"$set": profile})
    print(f"[profile] Saved profile for {profile['name']}")


def get_default_profile() -> dict:
    return {
        "_id":                   "guest",
        "name":                  "Guest",
        "car_model":             None,
        "price_sensitivity":     "any price",
        "ac_usage":              "sometimes",
        "prefer_fast_charging":  True,
        "min_battery_threshold": 10,
        "max_detour_km":         5,
        "saved_destinations":    [],
        "liked_stations":        [],
        "avoided_stations":      [],
        "preferences":           {"detour": [], "speed": [], "price": [], "access": []},
    }


# ── Station liked / avoided ───────────────────────────────────────────────────

def is_station_liked(profile: dict, station_id) -> bool:
    for s in profile.get("liked_stations", []):
        sid = s["id"] if isinstance(s, dict) else s
        if str(sid) == str(station_id):
            return True
    return False


def is_station_avoided(profile: dict, station_id) -> bool:
    for s in profile.get("avoided_stations", []):
        sid = s["id"] if isinstance(s, dict) else s
        if str(sid) == str(station_id):
            return True
    return False


def add_liked_station(profile: dict, station_id: str, station_name: str, tag: str) -> dict:
    """Add station to liked permanently. Removes from avoided if present."""
    profile["avoided_stations"] = [
        s for s in profile.get("avoided_stations", [])
        if str(s["id"] if isinstance(s, dict) else s) != str(station_id)
    ]
    already_liked = any(
        str(s["id"] if isinstance(s, dict) else s) == str(station_id)
        for s in profile.get("liked_stations", [])
    )
    if not already_liked:
        profile.setdefault("liked_stations", []).append({
            "id":        station_id,
            "name":      station_name,
            "tag":       tag,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    return profile


def add_avoided_station(profile: dict, station_id: str, station_name: str, tag: str) -> dict:
    """Add station to avoided permanently. Removes from liked if present."""
    profile["liked_stations"] = [
        s for s in profile.get("liked_stations", [])
        if str(s["id"] if isinstance(s, dict) else s) != str(station_id)
    ]
    already_avoided = any(
        str(s["id"] if isinstance(s, dict) else s) == str(station_id)
        for s in profile.get("avoided_stations", [])
    )
    if not already_avoided:
        profile.setdefault("avoided_stations", []).append({
            "id":        station_id,
            "name":      station_name,
            "tag":       tag,
            "permanent": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    return profile


def add_saved_destination(profile: dict, label: str, name: str, lat: float, lng: float) -> dict:
    destination = {"label": label, "name": name, "lat": lat, "lng": lng}
    profile["saved_destinations"] = [
        d for d in profile["saved_destinations"]
        if d["label"].lower() != label.lower()
    ]
    profile["saved_destinations"].append(destination)
    save_profile(profile)
    return profile


def get_saved_destination(profile: dict, label: str) -> dict | None:
    for d in profile["saved_destinations"]:
        if d["label"].lower() == label.lower():
            return d
    return None


# ── Preference signal system ──────────────────────────────────────────────────

TAG_TO_CRITERION = {
    "too far out of my way": "detour",
    "need a faster charger": "speed",
    "want a cheaper option": "price",
    "need public access":    "access",
}


def add_preference_signal(profile: dict, tag: str) -> dict:
    """Record a recommendation feedback signal with timestamp for exponential decay."""
    criterion = TAG_TO_CRITERION.get(tag.lower())
    if not criterion:
        return profile
    profile.setdefault("preferences", {"detour": [], "speed": [], "price": [], "access": []})
    profile["preferences"].setdefault(criterion, [])
    profile["preferences"][criterion].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    print(f"[profile] Preference signal added: {criterion} (tag: {tag})")
    return profile


def _decay_weight(timestamp_str: str) -> float:
    """
    Exponential decay: weight = 0.5 ^ (days_old / 30)
    30 days → 0.5, 60 days → 0.25, 180 days → ~0.03 (near zero)
    """
    try:
        ts       = datetime.fromisoformat(timestamp_str)
        now      = datetime.now(timezone.utc)
        days_old = (now - ts).days
        return 0.5 ** (days_old / 30)
    except Exception:
        return 0.0


def get_active_preference(profile: dict) -> str | None:
    """
    Calculate effective score per criterion using exponential decay.
    Returns the dominant criterion if it accounts for >= 50% of total.
    Returns None if no dominant preference — fall back to total_score sort.
    """
    prefs = profile.get("preferences", {})

    effective = {}
    for criterion, complaints in prefs.items():
        effective[criterion] = sum(_decay_weight(c["timestamp"]) for c in complaints)

    total = sum(effective.values())
    if total == 0:
        return None

    for criterion, score in effective.items():
        if score / total >= 0.5:
            print(f"[profile] Active preference: {criterion} ({round(score/total*100)}% of signals)")
            return criterion

    print(f"[profile] No dominant preference — using total score")
    return None


def get_avoided_station_context(profile: dict) -> str:
    """Return avoided stations summary for LLM injection."""
    avoided = [
        s for s in profile.get("avoided_stations", [])
        if isinstance(s, dict) and s.get("tag")
    ]
    if not avoided:
        return ""
    lines = ["AVOIDED STATIONS (do not recommend these):"]
    for s in avoided:
        lines.append(f"- {s['name']} (reason: {s['tag']})")
    return "\n".join(lines)