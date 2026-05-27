from fastapi import FastAPI, UploadFile, File
import os
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from groq import Groq


from backend.llm import clear_last_map_data, get_seva_response, clear_memory, get_last_map_data, get_chat_history, set_charging_mode
from backend.profile import load_profile, create_profile, save_profile, profile_exists, add_liked_station, add_avoided_station, add_preference_signal
from data.ev_helper import get_all_brands, get_models_by_brand, get_car_specs
from backend.geocoding import geocode

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatReq(BaseModel):
    message: str
    session_id: str
    profile: dict

class LoginReq(BaseModel):
    name: str
    pin: str

class RegisterReq(BaseModel):
    name: str
    pin: str
    car_model: str
    price_sensitivity: str
    ac_usage: str
    min_battery_threshold: int
    max_detour_km: int
    saved_destinations: list

class UpdateProfileReq(BaseModel):
    name: str
    pin: str
    car_model: str
    price_sensitivity: str
    ac_usage: str
    min_battery_threshold: int
    max_detour_km: int
    saved_destinations: list
    
class FeedbackReq(BaseModel):
    session_id:    str
    station_id:    str
    station_name:  str
    feedback_type: str   # "liked" | "avoided" | "preference"
    tag:           str
 

@app.post("/update-profile")
async def update_profile(req: UpdateProfileReq):
    profile = load_profile(req.name, req.pin)
    if not profile:
        return {"ok": False, "error": "Profile not found."}

    # ← Add geocoding here too
    for dest in req.saved_destinations:
        if not dest.get("lat") or not dest.get("lng"):
            coords = geocode(dest["name"])
            if coords:
                dest["lat"] = coords[0]
                dest["lng"] = coords[1]

    profile["car_model"]             = req.car_model
    profile["price_sensitivity"]     = req.price_sensitivity
    profile["ac_usage"]              = req.ac_usage
    profile["min_battery_threshold"] = req.min_battery_threshold
    profile["max_detour_km"]         = req.max_detour_km
    profile["saved_destinations"]    = req.saved_destinations
    specs = get_car_specs(req.car_model)
    profile.update(specs)
    save_profile(profile)
    return {"ok": True, "profile": profile}

@app.post("/chat")
async def chat(req: ChatReq):
    name = req.profile.get("name", "")
    pin  = req.profile.get("pin", "")
    fresh_profile = load_profile(name, pin)
    profile_to_use = fresh_profile if fresh_profile else req.profile

    profile_to_use["current_location"] = req.profile.get("current_location")
    profile_to_use["battery_pct"]      = req.profile.get("battery_pct", 100)

    response = get_seva_response(req.message, req.session_id, profile_to_use)
    map_data = get_last_map_data()
    return {
        "response": response,
        "map_data": map_data,
    }

@app.post("/login")
async def login(req: LoginReq):
    profile = load_profile(req.name, req.pin)
    if profile:
        if profile.get("car_model"):
            specs = get_car_specs(profile["car_model"])
            profile.update(specs)

        # Geocode any destinations missing coordinates
        updated = False
        for dest in profile.get("saved_destinations", []):
            if not dest.get("lat") or not dest.get("lng"):
                coords = geocode(dest["name"])
                if coords:
                    dest["lat"] = coords[0]
                    dest["lng"] = coords[1]
                    updated = True
        if updated:
            save_profile(profile)

        clear_last_map_data()

        return {"ok": True, "profile": profile}
    return {"ok": False, "error": "Name or PIN is incorrect."}

@app.post("/register")
async def register(req: RegisterReq):
    if profile_exists(req.name, req.pin):
        return {"ok": False, "error": "Profile already exists."}

    for dest in req.saved_destinations:
        coords = geocode(dest["name"])
        dest["lat"] = coords[0] if coords else None
        dest["lng"] = coords[1] if coords else None

    profile = create_profile(
        req.name, req.pin, req.car_model, req.price_sensitivity,
        req.ac_usage, req.min_battery_threshold, req.max_detour_km,
        req.saved_destinations  # ← now includes lat/lng
    )
    specs = get_car_specs(req.car_model)
    profile.update(specs)

    return {"ok": True, "profile": profile}

@app.get("/history/{session_id}")
async def history(session_id: str):
    return {"history": get_chat_history(session_id)}

@app.post("/clear-memory")
async def clear(data: dict):
    clear_memory(data["session_id"])
    clear_last_map_data()
    return {"ok": True}

@app.post("/feedback")
async def feedback(req: FeedbackReq):
    """
    Three feedback types:
    - liked:      station permanently liked (saved to liked_stations)
    - avoided:    station permanently avoided (saved to avoided_stations)
    - preference: recommendation signal for future sorting (exponential decay)
    """
    parts = req.session_id.rsplit("_", 1)
    if len(parts) != 2:
        return {"ok": False, "error": "Invalid session_id format"}
 
    name, pin = parts[0], parts[1]
    profile   = load_profile(name, pin)
    if not profile:
        return {"ok": False, "error": "Profile not found"}
 
    if req.feedback_type == "liked":
        profile = add_liked_station(profile, req.station_id, req.station_name, req.tag)
        save_profile(profile)
        return {"ok": True, "message": f"{req.station_name} added to liked stations."}
 
    elif req.feedback_type == "avoided":
        profile = add_avoided_station(profile, req.station_id, req.station_name, req.tag)
        save_profile(profile)
        return {"ok": True, "message": f"{req.station_name} added to avoided stations."}
 
    elif req.feedback_type == "preference":
        profile = add_preference_signal(profile, req.tag)
        save_profile(profile)
        return {"ok": True, "message": "Preference noted."}
 
    else:
        return {"ok": False, "error": f"Unknown feedback_type: {req.feedback_type}"}

@app.post("/set_charging_mode")
def set_mode(payload: dict):
    mode = payload.get("mode", "charge_to_80")
    set_charging_mode(mode)
    return {"ok": True}

@app.get("/brands")
async def brands():
    return {"brands": get_all_brands()}

@app.get("/models/{brand}")
async def models(brand: str):
    return {"models": get_models_by_brand(brand)}

@app.get("/specs/{model}")
async def specs(model: str):
    return {"specs": get_car_specs(model)}

@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    """
    Receives a recorded audio file from the browser,
    sends it to Groq Whisper for transcription + translation to English,
    returns the transcribed text.
    """
    client  = Groq(api_key=os.getenv("GROQ_API_KEY"))
    content = await audio.read()

    try:
        transcription = client.audio.transcriptions.create(
            file=(audio.filename or "recording.webm", content),
            model="whisper-large-v3-turbo",
            response_format="text",
            # Translation happens automatically when no language is forced
            # Whisper detects Arabic/English and outputs English text
            prompt=(
                "Egyptian EV driver asking about charging stations in Egypt. "
                "Place names include: Maadi, New Cairo, Nasr City, AUC, GUC, "
                "Cairo Festival City, Heliopolis, Zamalek, 6th of October, "
                "Sheikh Zayed, Tagamoa, Rehab, Katameya. "
                "Arabic place names: مدينة نصر، التجمع، المعادي، الزمالك، "
                "مصر الجديدة، القاهرة الجديدة، الشيخ زايد."
            ),
        )
        return {"text": str(transcription).strip()}

    except Exception as e:
        print(f"[transcribe] Error: {e}")
        return {"text": "", "error": str(e)}
    
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)