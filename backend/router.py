"""
router.py — SEVA Brain
Owns: personality, conversation, intent classification, general answers, followup answers.
The recommendation agent (llm.py) is only called for new trip requests.
"""

import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

_router_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ── SEVA Personality & Classification Prompt ──────────────────────────────────
_ROUTER_SYSTEM = """You are SEVA, an intelligent Electric Vehicle charging assistant for Egyptian drivers.
You help EV drivers find the best charging station — route-aware, not just the nearest.
You are friendly, concise, and knowledgeable about EVs and charging in Egypt.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR ROLE IN THIS SYSTEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You have TWO responsibilities depending on the task you receive:

TASK 1 — INTENT CLASSIFICATION:
  Read the user message and return a JSON object classifying their intent.

TASK 2 — DIRECT RESPONSE:
  For "general" and "followup" intents, you generate the response directly.
  For "recommendation" and "refine", another system handles it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SEVA PERSONALITY AND RESPONSE RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Be helpful, warm, and direct. No filler phrases like "Great question!" or "Of course!"
- Keep answers SHORT — maximum 2-3 sentences unless detailed explanation is needed.
- Never use HTML tags or markdown code blocks.
- Never include GPS coordinates or lat/lng numbers in responses.
- Say "your current location" instead of coordinates.
- Plain text only.
- Only help with EV-related topics. Politely decline unrelated questions.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EV KNOWLEDGE — EGYPT CONTEXT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Charging prices in Egypt:
- AC slow charging: 3.97 EGP per kWh
- DC fast charging: 7.67 EGP per kWh
Cost formula: Energy (kWh) = (battery_kwh x percentage_to_charge / 100), Cost = Energy x Rate

Charging types:
- AC Level 2: up to 22kW, slower, cheaper, widely available
- DC fast charge: 50kW to 120kW+, much faster, more expensive
- Charging above 80% is slower due to battery thermal protection curve
- Optimal charging stop: charge to 80%, then continue your trip

Egyptian EV operators: TAQA Volt, Elsewedy Plug, Revolta, IKARUS, Infinity EV,
Sha7en, Electra EG, Karm EG, MegaPlug, Electric Mobility

Common connector types in Egypt:
- Type 2: standard AC connector for most European and Asian EVs
- CCS2 (CCS Type 2): DC fast charging for most EVs
- GB/T: Chinese standard used by BYD, MG, Chery EVs common in Egypt
- CHAdeMO: older DC standard, rare in Egypt

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INTENT DEFINITIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
general        - EV knowledge questions, greetings, explanations, anything not requiring
                 station data. YOU answer these directly.
                 Also use for non-EV questions and politely decline them.

recommendation - User wants to find charging stations for a specific trip.
                 Needs destination and battery context. The recommendation agent handles this.

followup       - User asks about stations ALREADY recommended.
                 Same stations, more info: price, membership, charge time, connector,
                 comparisons between shown options. YOU answer these directly.

refine         - User wants DIFFERENT stations or re-ranking of previous recommendation.
                 The refine handler processes this, not you.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CLASSIFICATION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- If has_previous_stations=false, followup and refine are impossible.
- refine = user wants a DIFFERENT result. followup = user wants MORE INFO about SAME result.
- If unsure between recommendation and refine, use refine if previous stations exist.
- Low confidence defaults to recommendation.
- Non-EV questions are always general.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NORMALIZED MODIFICATION TAGS FOR REFINE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Use these exact tags in the modification field:
- "closer"           - user wants station with less detour
- "faster"           - user wants faster charging or less charge time
- "cheaper"          - user wants lower cost option
- "dc_fast_only"     - user wants DC fast charging only
- "exclude option N" - user wants to remove option N
- "battery_level=N"  - user changed battery level to N percent
- "destination=Place"- user changed destination
- "origin=Place"     - user changed origin

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CLASSIFICATION EXAMPLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
User: "hello" -> {"intent": "general", "confidence": "high"}
User: "what is a CCS connector?" -> {"intent": "general", "confidence": "high"}
User: "how does regenerative braking work?" -> {"intent": "general", "confidence": "high"}
User: "what is the weather today?" -> {"intent": "general", "confidence": "high"}

User: "I want to go from Maadi to New Cairo, battery at 45%" -> {"intent": "recommendation", "confidence": "high"}
User: "I need to charge, heading to Cairo Airport" -> {"intent": "recommendation", "confidence": "high"}
User: "my battery is at 20% and I need to reach Zamalek" -> {"intent": "recommendation", "confidence": "high"}

User: "how much will I pay at option 2?" -> {"intent": "followup", "confidence": "high"}
User: "do I need a membership there?" -> {"intent": "followup", "confidence": "high"}
User: "which one is faster?" -> {"intent": "followup", "confidence": "high"}
User: "which is better and why?" -> {"intent": "followup", "confidence": "high"}
User: "tell me more about option 3" -> {"intent": "followup", "confidence": "high"}

User: "show me something closer" -> {"intent": "refine", "confidence": "high", "modification": "closer"}
User: "i want a faster charger" -> {"intent": "refine", "confidence": "high", "modification": "faster"}
User: "i want less charging time" -> {"intent": "refine", "confidence": "high", "modification": "faster"}
User: "find me a cheaper option" -> {"intent": "refine", "confidence": "high", "modification": "cheaper"}
User: "i want another DC charger" -> {"intent": "refine", "confidence": "high", "modification": "dc_fast_only"}
User: "ignore the first option" -> {"intent": "refine", "confidence": "high", "modification": "exclude option 1"}
User: "what if my battery was at 20%?" -> {"intent": "refine", "confidence": "high", "modification": "battery_level=20"}
User: "going to Sheikh Zayed instead" -> {"intent": "refine", "confidence": "high", "modification": "destination=Sheikh Zayed"}
User: "actually starting from Madinty" -> {"intent": "refine", "confidence": "high", "modification": "origin=Madinty"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT FOR CLASSIFICATION TASK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
When classifying: respond ONLY with a valid JSON object. No markdown, no explanation.
Required: intent, confidence (high or low).
Optional: modification (string, refine only, use normalized tags).
"""

# ── SEVA Conversational Prompt ────────────────────────────────────────────────
_SEVA_SYSTEM = """You are SEVA, an intelligent Electric Vehicle charging assistant for Egyptian drivers.
Be helpful, warm, and direct. No filler phrases. Plain text only. Max 3 sentences unless more detail is needed.
Only answer EV-related questions. Politely decline anything unrelated to EVs.

Charging prices in Egypt:
- AC slow charging: 3.97 EGP per kWh
- DC fast charging: 7.67 EGP per kWh
Cost formula: Energy (kWh) = (battery_kwh x percentage_to_charge / 100), Cost = Energy x Rate

Charging types:
- AC Level 2: up to 22kW, slower, cheaper
- DC fast charge: 50kW to 120kW+, faster, more expensive
- Charging above 80% is slower — optimal stop is 80%

Egyptian EV operators: TAQA Volt, Elsewedy Plug, Revolta, IKARUS, Infinity EV, Sha7en, Electra EG, Karm EG, MegaPlug, Electric Mobility
Connectors: Type 2 (AC), CCS2 (DC fast), GB/T (BYD/MG/Chery), CHAdeMO (rare)
"""

def classify_intent(
    user_message: str,
    has_previous_stations: bool,
    recent_history: list[dict] | None = None
) -> dict:
    """Classify intent into general, recommendation, followup, or refine."""
    history_str = ""
    if recent_history:
        lines = []
        for msg in recent_history[-3:]:
            role    = msg.get("role", "user")
            content = msg.get("content", "")[:200]
            lines.append(f"{role}: {content}")
        history_str = "\nRecent conversation:\n" + "\n".join(lines)

    prompt = (
        f"{history_str}\n\n"
        f"has_previous_stations: {str(has_previous_stations).lower()}\n"
        f"User message: \"{user_message}\"\n\n"
        f"Classify the intent. Respond with JSON only:"
    )

    raw = ""  # ← initialize before try so except block can always access it

    try:
        for attempt in range(2):  # ← retry once on empty response
            response = _router_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": _ROUTER_SYSTEM},
                    {"role": "user",   "content": prompt}
                ],
                temperature=0.0,
                max_tokens=150,
            )
            raw = response.choices[0].message.content.strip()

            if "<think>" in raw:
                raw = raw.split("</think>")[-1].strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            if raw:
                break
            print(f"[router] Empty response on attempt {attempt + 1}, retrying...")

        if not raw:
            print("[router] All attempts returned empty — returning error intent")
            return {"intent": "error", "confidence": "low", "modification": None}

        result = json.loads(raw)

        valid_intents = {"general", "recommendation", "followup", "refine", "ask_battery", "error"}
        if result.get("intent") not in valid_intents:
            raise ValueError(f"Invalid intent: {result.get('intent')}")

        if not has_previous_stations and result["intent"] in ("followup", "refine"):
            print(f"[router] Override '{result['intent']}' -> 'recommendation' (no cached stations)")
            result["intent"]     = "recommendation"
            result["confidence"] = "low"

        if result.get("confidence") == "low" and result["intent"] not in ("general", "recommendation"):
            print(f"[router] Low confidence -> 'recommendation'")
            result["intent"] = "recommendation"

        print(f"[router] Intent: {result['intent']} ({result.get('confidence')}) | mod: {result.get('modification')}")
        return result

    except Exception as e:
        print(f"[router] Classification failed: {e} — returning error intent")
        return {"intent": "error", "confidence": "low", "modification": None}
    
def answer_general(user_message: str) -> str:
    """Answer a general EV question using SEVA personality. Blocks non-EV questions."""
    try:
        response = _router_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": _SEVA_SYSTEM},
                {"role": "user",   "content": user_message}
            ],
            temperature=0.4,
            max_tokens=200,
        )
        raw = response.choices[0].message.content.strip()
        if "<think>" in raw:
            raw = raw.split("</think>")[-1].strip()
        return raw
    except Exception as e:
        print(f"[router] answer_general error: {e}")
        return "Sorry, I couldn't process that. Please try again."


def answer_followup(
    user_message: str,
    last_map_data: list,
    battery_kwh: float,
    current_battery: int
) -> str:
    """Answer a followup question using SEVA personality and cached station data."""
    if not last_map_data:
        return "I don't have any recent station recommendations. Please ask for a new recommendation first."

    parts = []
    for i, s in enumerate(last_map_data):
        try:
            rate     = s.get("rate_egp_kwh", 7.67)
            target   = s.get("target_battery", 80)
            energy   = ((target - current_battery) / 100) * battery_kwh
            cost_str = f"{round(energy * rate, 1)} EGP"
        except Exception:
            cost_str = "unknown"

        parts.append(
            f"Option {i+1}: {s.get('station_name')} | "
            f"Operator: {s.get('operator')} | "
            f"Connectors: {s.get('connectors')} | "
            f"Fast charge: {'Yes' if s.get('has_fast_charge') else 'No'} | "
            f"Charge time: ~{s.get('charge_time')} min to {s.get('target_battery')}% | "
            f"Estimated cost: {cost_str} | "
            f"Access: {'Members only' if s.get('needs_membership') else 'Public'} | "
            f"Score: {s.get('score')}/100 | "
            f"Detour: {s.get('detour_km', 0)} km | "
            f"Rate: {s.get('rate_egp_kwh')} EGP/kWh | "
            f"Trip duration: {s.get('route_duration_min')} min | "
            f"Trip distance: {s.get('route_distance_km')} km"
        )

    station_context = "RECOMMENDED STATIONS:\n" + "\n".join(parts)

    followup_prompt = (
        f"Car battery size: {battery_kwh} kWh. Current battery: {current_battery}%.\n"
        f"Estimated cost per option is pre-calculated — use it directly.\n"
        f"Answer ONLY what was asked. Max 2 sentences. No filler.\n\n"
        f"{station_context}\n\n"
        f"User question: {user_message}"
    )

    try:
        response = _router_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": _SEVA_SYSTEM},
                {"role": "user",   "content": followup_prompt}
            ],
            temperature=0.3,
            max_tokens=200,
        )
        raw = response.choices[0].message.content.strip()
        if "<think>" in raw:
            raw = raw.split("</think>")[-1].strip()
        return raw
    except Exception as e:
        print(f"[router] answer_followup error: {e}")
        return "I couldn't retrieve that information. Please try rephrasing."