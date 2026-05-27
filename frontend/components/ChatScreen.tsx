"use client";

import { useState, useRef, useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
import { sendMessage, clearMemory } from "../lib/api";
import { useJsApiLoader, GoogleMap, Marker, Polyline } from "@react-google-maps/api";

interface Message {
  role: "user" | "assistant";
  content: string;
  parsed?: ParsedResponse;
  mapData?: StationMapData[];   // now a list of up to 3
  restored?: boolean;
}

interface StationMapData {
  station_id:         string;
  station_name:       string;
  station_lat:        number;
  station_lng:        number;
  origin_lat:         number;
  origin_lng:         number;
  dest_lat:           number;
  dest_lng:           number;
  origin_name:        string;
  dest_name:          string;
  operator:           string;
  connectors:         string;
  has_fast_charge:    boolean;
  charge_time:        number | string;
  target_battery:     number;
  score:              number | string;
  available:          boolean;
  needs_membership:   boolean;
  route_distance_km:  number;
  route_duration_min: number;
  rate_egp_kwh:       number;
  detour_km:          number;
}

interface ParsedResponse {
  intro:    string;
  hasCards: boolean;   // true when map_data has stations
}

interface Props {
  profile: any;
  onLogout: () => void;
  onUpdateProfile: () => void;
}

function parseResponse(text: string, mapData: StationMapData[] | null): ParsedResponse {
  const lines = text.trim().split("\n");

  const intro = lines.find(l => {
    const clean = l.trim();
    return clean.length > 0
      && !clean.startsWith("[")
      && !clean.startsWith("Option ")
      && !clean.startsWith("TOP 3")
      && !clean.startsWith("ROUTE:")
      && !clean.startsWith("Write ")
      && !clean.startsWith("-")
      && !clean.startsWith("*");
  }) || "Here are the recommendations for your route.";

  return {
    intro,
    hasCards: !!(mapData && mapData.length > 0),
  };
}

export default function ChatScreen({ profile, onLogout, onUpdateProfile }: Props) {
  const [messages, setMessages]             = useState<Message[]>([]);
  const [input, setInput]                   = useState("");
  const [loading, setLoading]               = useState(false);
  const [loadingMessage, setLoadingMessage] = useState("Thinking...");
  const [likedCount, setLikedCount]         = useState(0);
  const sessionId                           = `${profile.name.toLowerCase()}_${profile.pin}`;
  const [openMapKey, setOpenMapKey]         = useState<string | null>(null); // "msgIdx_stationIdx"
  const [batteryPct, setBatteryPct]         = useState<number>(profile.battery_pct || 100);
  const bottomRef                           = useRef<HTMLDivElement>(null);
  const [userLocation, setUserLocation]     = useState<{lat: number, lng: number} | null>(null);
  const [awaitingChargingMode, setAwaitingChargingMode] = useState(false);
  const [pendingMessage, setPendingMessage]             = useState<string | null>(null);
  const [recording, setRecording]                       = useState(false);
  const [transcribing, setTranscribing]                 = useState(false);
  const mediaRecorderRef                                = useRef<MediaRecorder | null>(null);
  const audioChunksRef                                  = useRef<Blob[]>([]);
  const [isMobile, setIsMobile]                         = useState(false);
  const [sidebarOpen, setSidebarOpen]                   = useState(false);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  useEffect(() => {
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        pos => setUserLocation({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
        err => console.log("Location denied:", err)
      );
    }
  }, []);

  useEffect(() => {
    const sid = `${profile.name.toLowerCase()}_${profile.pin}`;
    fetch(`${API_BASE}/history/${sid}`)
      .then(res => res.json())
      .then(data => {
        if (data.history && data.history.length > 0) {
          const formatted = data.history.map((msg: any) => ({
            ...msg,
            parsed:   msg.role === "assistant" ? parseResponse(msg.content, null) : undefined,
            restored: true,
            mapData:  null,
          }));
          setMessages(formatted);
        }
      })
      .catch(err => console.error("Failed to load history:", err));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  function extractBatteryFromMessage(msg: string): number | null {
    const match = msg.match(/(\d{1,3})\s*%/);
    if (match) {
      const val = parseInt(match[1]);
      if (val >= 1 && val <= 100) return val;
    }
    return null;
  }

  function getLoadingMessage(msg: string): string {
    const lower = msg.toLowerCase();
    if (looksLikeTrip(msg))                                               return "Finding the best chargers along your route ...";
    if (lower.includes("price") || lower.includes("cost") || lower.includes("pay")) return "Calculating charging cost ...";
    if (lower.includes("how long") || lower.includes("time"))             return "Estimating charge time ...";
    if (lower.includes("map") || lower.includes("where"))                 return "Looking up the location ...";
    if (lower.includes("fast") || lower.includes("connector"))            return "Checking connector details ...";
    if (lower.includes("detour") || lower.includes("far"))                return "Checking route details ...";
    return "Thinking ...";
  }

  function looksLikeTrip(msg: string): boolean {
    const lower = msg.toLowerCase();
    const hasMovement =
      lower.includes("going to") || lower.includes("heading to") ||
      lower.includes("destination") || lower.includes("driving to") ||
      lower.includes("driving from") || lower.includes("travelling to") ||
      lower.includes("traveling to") ||
      (lower.includes("from") && lower.includes("to")) ||
      lower.includes("i want to go") || lower.includes("i am going") ||
      lower.includes("i'm going") || lower.includes("i need to get to") ||
      lower.includes("route");
    const hasBattery =
      lower.includes("battery") || lower.includes("%") || lower.includes("charge");
    return hasMovement && hasBattery;
  }

  // ── Audio recording ──────────────────────────────────────────────────────
  async function handleRecord() {
    if (recording) {
      mediaRecorderRef.current?.stop();
      setRecording(false);
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current   = [];

      mediaRecorder.ondataavailable = e => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const audioBlob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        setTranscribing(true);
        try {
          const formData = new FormData();
          formData.append("audio", audioBlob, "recording.webm");
          const res  = await fetch(`${API_BASE}/transcribe`, {
            method: "POST",
            body: formData,
          });
          const data = await res.json();
          if (data.text) setInput(data.text.trim());
        } catch (err) {
          console.error("Transcription failed:", err);
        }
        setTranscribing(false);
      };

      mediaRecorder.start();
      setRecording(true);
    } catch (err) {
      console.error("Microphone access denied:", err);
    }
  }

  // ── Get GPS, re-requesting if not yet captured ────────────────────────────
  async function getLocation(): Promise<{lat: number, lng: number} | null> {
    if (userLocation) return userLocation;
    if (!navigator.geolocation) return null;
    return new Promise(resolve => {
      navigator.geolocation.getCurrentPosition(
        pos => {
          const loc = { lat: pos.coords.latitude, lng: pos.coords.longitude };
          setUserLocation(loc);
          resolve(loc);
        },
        () => resolve(null),
        { timeout: 4000, maximumAge: 60000 }
      );
    });
  }

  async function handleSend(text?: string) {
    const msg = (text || input).trim();
    if (!msg || loading) return;
    setInput("");

    const msgBattery     = extractBatteryFromMessage(msg);
    const currentBattery = msgBattery ?? batteryPct;
    if (msgBattery !== null) setBatteryPct(msgBattery);

    if (looksLikeTrip(msg) && !awaitingChargingMode) {
      setMessages(prev => [...prev, { role: "user", content: msg }]);
      setPendingMessage(msg);
      setAwaitingChargingMode(true);
      return;
    }

    setMessages(prev => [...prev, { role: "user", content: msg }]);
    setLoadingMessage(getLoadingMessage(msg));
    setLoading(true);

    try {
      const res    = await sendMessage(msg, sessionId, {
        ...profile, battery_pct: currentBattery, current_location: userLocation,
      });
      const mapData = Array.isArray(res.map_data) ? res.map_data : null;
      const parsed  = parseResponse(res.response || "", mapData);
      setMessages(prev => [...prev, {
        role: "assistant", content: res.response, parsed, mapData,
      }]);
    } catch {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: "Sorry, I couldn't connect to the server. Please try again.",
        parsed: parseResponse("", null),
      }]);
    }
    setLoading(false);
  }

  function handleNewTrip() {
    setMessages([]);
    clearMemory(sessionId);
    setOpenMapKey(null);
    setAwaitingChargingMode(false);
    setPendingMessage(null);
  }

  async function handleChargingMode(mode: "complete_trip" | "charge_to_80" | "charge_to_100") {
    if (!pendingMessage) return;
    setAwaitingChargingMode(false);

    const modeLabels = {
      complete_trip: "Just enough to complete the trip",
      charge_to_80:  "Charge to 80%",
      charge_to_100: "Charge to 100%",
    };

    setMessages(prev => [...prev, { role: "user", content: modeLabels[mode] }]);
    setLoadingMessage("Finding the best chargers along your route ...");
    setLoading(true);

    const msgBattery     = extractBatteryFromMessage(pendingMessage);
    const currentBattery = msgBattery ?? batteryPct;

    try {
      await fetch(`${API_BASE}/set_charging_mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });

      const res    = await sendMessage(pendingMessage, sessionId, {
        ...profile, battery_pct: currentBattery, current_location: userLocation,
      });
      const mapData = Array.isArray(res.map_data) ? res.map_data : null;
      const parsed  = parseResponse(res.response || "", mapData);
      setMessages(prev => [...prev, {
        role: "assistant", content: res.response, parsed, mapData,
      }]);
    } catch {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: "Sorry, I couldn't connect to the server. Please try again.",
        parsed: parseResponse("", null),
      }]);
    }

    setLoading(false);
    setPendingMessage(null);
  }

  // ── Feedback submit ────────────────────────────────────────────────────────
  async function handleFeedbackSubmit(
    stationId: string, stationName: string,
    type: string, tag: string
  ) {
    try {
      await fetch(`${API_BASE}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id:    sessionId,
          station_id:    stationId,
          station_name:  stationName,
          feedback_type: type,
          tag,
        }),
      });
      if (type === "liked") setLikedCount(c => c + 1);
    } catch (err) {
      console.error("Feedback submit failed:", err);
    }
  }

  const savedDests   = profile.saved_destinations || [];
  const batteryColor = batteryPct < 20 ? "#E24B4A" : batteryPct < 50 ? "var(--accent)" : "var(--status-dot)";

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: "var(--bg)" }}>
      {/* ── Topbar ── */}
      <div style={{
        background: "var(--primary)", padding: "10px 20px", flexShrink: 0,
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {/* Hamburger — mobile only */}
          {isMobile && (
            <button
              onClick={() => setSidebarOpen(o => !o)}
              style={{
                background: "none", border: "none", padding: "2px 6px 2px 0",
                cursor: "pointer", display: "flex", flexDirection: "column",
                gap: 4, flexShrink: 0,
              }}>
              <span style={{ display: "block", width: 16, height: 1.5, background: "var(--accent)", borderRadius: 1 }} />
              <span style={{ display: "block", width: 16, height: 1.5, background: "var(--accent)", borderRadius: 1 }} />
              <span style={{ display: "block", width: 16, height: 1.5, background: "var(--accent)", borderRadius: 1 }} />
            </button>
          )}
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--accent)" }} />
          <span style={{ color: "#F5F2EA", fontSize: isMobile ? 13 : 15, fontWeight: 500 }}>
            {isMobile ? "SEVA" : "SEVA — Smart EV Assistant"}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted2)" }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--status-dot)" }} />
          {!isMobile && "Connected"}
        </div>
      </div>

      <div style={{ display: "flex", flex: 1, overflow: "hidden", position: "relative" }}>

        {/* ── Mobile overlay ── */}
        {isMobile && sidebarOpen && (
          <div
            onClick={() => setSidebarOpen(false)}
            style={{
              position: "absolute", inset: 0, background: "rgba(0,0,0,0.4)",
              zIndex: 10,
            }}
          />
        )}

        {/* ── Sidebar ── */}
        <div style={{
          width: 220, background: "var(--primary)", padding: 16,
          display: "flex", flexDirection: "column", gap: 12,
          flexShrink: 0, overflowY: "auto",
          ...(isMobile ? {
            position:   "absolute",
            top:        0,
            left:       0,
            bottom:     0,
            zIndex:     11,
            transform:  sidebarOpen ? "translateX(0)" : "translateX(-100%)",
            transition: "transform 0.25s ease",
          } : {}),
        }}>
          {/* Close button — mobile only */}
          {isMobile && (
            <button
              onClick={() => setSidebarOpen(false)}
              style={{
                background: "none", border: "none", color: "var(--muted2)",
                fontSize: 12, cursor: "pointer", alignSelf: "flex-end",
                padding: "0 0 4px", display: "flex", alignItems: "center", gap: 4,
              }}>
              <i className="ti ti-x" style={{ fontSize: 14 }} /> Close
            </button>
          )}

          <div style={{ background: "var(--primary2)", borderRadius: 10, padding: 12 }}>
            <div style={{ color: "#F5F2EA", fontSize: 13, fontWeight: 500 }}>{profile.name}</div>
            <div style={{ color: "var(--muted2)", fontSize: 11, marginTop: 2, marginBottom: 10 }}>{profile.car_model}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 5, paddingBottom: 10, borderBottom: "0.5px solid #1E3A2F" }}>
              {(profile.battery_kwh || profile.battery_kWh) && (
                <SpecItem icon="ti-battery-2" label={`${profile.battery_kwh || profile.battery_kWh} kWh battery`} />
              )}
              {(profile.dc_connector || profile.max_dc_kw) && (
                <SpecItem icon="ti-bolt" label={`${profile.dc_connector || "DC"} · ${profile.max_dc_kw || ""}kW`} />
              )}
              {(profile.ac_connector || profile.max_ac_kw) && (
                <SpecItem icon="ti-plug" label={`${profile.ac_connector || "AC"} · ${profile.max_ac_kw || ""}kW`} />
              )}
              {profile.range_km && (
                <SpecItem icon="ti-road" label={`${profile.range_km} km range`} />
              )}
            </div>
            <div style={{ marginTop: 10 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--muted2)", marginBottom: 6 }}>
                <span>Current battery</span>
                <span style={{ color: batteryColor, fontWeight: 600 }}>{batteryPct}%</span>
              </div>
              <div style={{ background: "var(--primary)", borderRadius: 4, height: 5, marginBottom: 6 }}>
                <div style={{ background: batteryColor, borderRadius: 4, height: 5, width: `${batteryPct}%`, transition: "width 0.2s" }} />
              </div>
              <input type="range" min={1} max={100} value={batteryPct}
                onChange={e => setBatteryPct(+e.target.value)}
                style={{ width: "100%", accentColor: batteryColor, cursor: "pointer", margin: 0 }}
              />
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "var(--muted2)", marginTop: 2 }}>
                <span>1%</span><span>100%</span>
              </div>
            </div>
          </div>

          <div>
            <div style={sidebarLabelStyle}>Session</div>
            <div style={{ display: "flex", gap: 8 }}>
              <StatBox num={messages.filter(m => m.role === "user" && ![
                "Just enough to complete the trip",
                "Charge to 80%",
                "Charge to 100%",
              ].includes(m.content)).length} label="Queries" />
              <StatBox num={likedCount} label="Liked" />
            </div>
          </div>

          <button onClick={() => { handleNewTrip(); setSidebarOpen(false); }} style={{
            background: "var(--accent)", color: "var(--primary)", border: "none",
            borderRadius: 8, padding: 9, fontSize: 12, fontWeight: 600, width: "100%",
          }}>
            + New Trip
          </button>

          <hr style={{ border: "none", borderTop: "0.5px solid var(--primary2)", margin: "2px 0" }} />
          <SidebarBtn icon="ti-edit"   label="Update Profile" onClick={() => { onUpdateProfile(); setSidebarOpen(false); }} />
          <SidebarBtn icon="ti-logout" label="Log Out"         onClick={() => { onLogout(); setSidebarOpen(false); }} />

          {savedDests.length > 0 && (
            <>
              <hr style={{ border: "none", borderTop: "0.5px solid var(--primary2)", margin: "2px 0" }} />
              <div>
                <div style={sidebarLabelStyle}>Saved destinations</div>
                {savedDests.map((dest: any) => {
                  const icon = dest.label?.toLowerCase() === "home" ? "ti-home"
                             : dest.label?.toLowerCase() === "work" ? "ti-briefcase" : "ti-map-pin";
                  return (
                    <button key={dest.label}
                      onClick={() => { setInput(`Going to ${dest.name}, battery at ${batteryPct}%`); setSidebarOpen(false); }}
                      style={{
                        background: "var(--primary2)", border: "none", borderRadius: 8,
                        padding: "8px 10px", width: "100%", textAlign: "left",
                        marginBottom: 6, cursor: "pointer",
                        display: "flex", alignItems: "center", gap: 8,
                      }}>
                      <i className={`ti ${icon}`} style={{ fontSize: 13, color: "var(--accent)", flexShrink: 0 }} />
                      <div>
                        <div style={{ color: "#F5F2EA", fontSize: 11, fontWeight: 500 }}>{dest.label}</div>
                        <div style={{ color: "var(--muted2)", fontSize: 10, marginTop: 1 }}>{dest.name}</div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>

        {/* ── Main chat area ── */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ flex: 1, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 16 }}>

            {messages.length === 0 && !loading && !awaitingChargingMode && (
              <div style={{
                flex: 1, display: "flex", flexDirection: "column",
                alignItems: "center", justifyContent: "center",
                padding: "40px 60px", textAlign: "center",
              }}>
                <div style={{ fontSize: 32, fontWeight: 600, color: "var(--primary)", marginBottom: 16, lineHeight: 1.3 }}>
                  Where are you heading today, {profile.name}?
                </div>
                <div style={{ fontSize: 15, color: "var(--muted)", lineHeight: 1.7, maxWidth: 420 }}>
                  Type your route and current battery level and I'll find the best
                  charging stations for your {profile.car_model}.
                </div>
              </div>
            )}

            {messages.map((msg, msgIdx) => (
              <div key={msgIdx}>
                {msg.role === "user" ? (
                  <div style={{ display: "flex", justifyContent: "flex-end" }}>
                    <div style={{
                      background: "var(--primary)", color: "#F5F2EA",
                      borderRadius: "12px 12px 2px 12px",
                      padding: "10px 14px", maxWidth: "70%", fontSize: 13, lineHeight: 1.5,
                    }}>
                      {msg.content}
                    </div>
                  </div>
                ) : (
                  <div style={{ maxWidth: "92%" }}>
                    {/* Intro bubble */}
                    <div style={{
                      background: "var(--white)",
                      border: "0.5px solid var(--border)",
                      borderLeft: "3px solid var(--primary)",
                      borderRadius: "0 12px 12px 12px",
                      padding: "12px 14px", fontSize: 13, color: "var(--text)", lineHeight: 1.6,
                    }}>
                      <p style={{ margin: 0 }}>{msg.parsed?.intro || msg.content}</p>
                    </div>
                    {/* ── Restored message note ── */}
                    {msg.restored && (
                      <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 6,
                        display: "flex", alignItems: "center", gap: 4,
                    }}>
                        <i className="ti ti-history" style={{ fontSize: 11 }} />
                        Past recommendation — start a new trip to see updated options
                      </div>
                    )}
                    {/* ── 3 Station cards ── */}
                    {msg.parsed?.hasCards && msg.mapData && !msg.restored && (
                      <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 10 }}>
                        {msg.mapData.map((station, sIdx) => {
                          const mapKey    = `${msgIdx}_${sIdx}`;
                          const isMapOpen = openMapKey === mapKey;
                          const rank      = sIdx + 1;

                          return (
                            <div key={sIdx}>
                              {/* Rank label */}
                              <div style={{
                                fontSize: 10, fontWeight: 600, color: "var(--muted2)",
                                textTransform: "uppercase", letterSpacing: 1,
                                marginBottom: 4, paddingLeft: 2,
                              }}>
                                {rank === 1 ? "#1 Best match" : rank === 2 ? "#2 Runner-up" : "#3 Alternative"}
                              </div>

                              {/* Station card */}
                              <StationCard station={station} />

                              {/* Map buttons */}
                              <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                                <button
                                  onClick={() => setOpenMapKey(isMapOpen ? null : mapKey)}
                                  style={{
                                    display: "flex", alignItems: "center", gap: 6,
                                    padding: "7px 14px", borderRadius: 8, fontSize: 12, fontWeight: 500,
                                    background: "var(--primary)", color: "#F5F2EA", border: "none", cursor: "pointer",
                                  }}>
                                  <i className="ti ti-map-pin" />
                                  {isMapOpen ? "Hide map" : "Preview on map"}
                                </button>
                                <a
                                  href={`https://www.google.com/maps/dir/?api=1&origin=${station.origin_lat},${station.origin_lng}&destination=${station.station_lat},${station.station_lng}&travelmode=driving`}
                                  target="_blank" rel="noreferrer"
                                  style={{
                                    display: "flex", alignItems: "center", gap: 6,
                                    padding: "7px 14px", borderRadius: 8, fontSize: 12, fontWeight: 500,
                                    background: "var(--accent)", color: "var(--primary)", textDecoration: "none",
                                  }}>
                                  <i className="ti ti-navigation" />
                                  Open in Google Maps
                                </a>
                              </div>

                              {/* Map preview — one at a time */}
                              {isMapOpen && (
                                <MapPreview
                                  station={station}
                                  onClose={() => setOpenMapKey(null)}
                                />
                              )}
                            </div>
                          );
                        })}

                        {/* ── Rate the experience — one button for all 3 ── */}
                        <RateExperience
                          stations={msg.mapData}
                          onSubmit={handleFeedbackSubmit}
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}

            {/* Charging mode picker */}
            {awaitingChargingMode && (
              <div style={{ maxWidth: "88%" }}>
                <div style={{
                  background: "var(--white)", border: "0.5px solid var(--border)",
                  borderLeft: "3px solid var(--primary)",
                  borderRadius: "0 12px 12px 12px", padding: "12px 14px",
                }}>
                  <p style={{ fontSize: 13, color: "var(--text)", marginBottom: 12, marginTop: 0 }}>
                    How would you like to charge on this trip?
                  </p>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {[
                      { mode: "complete_trip"  as const, icon: "ti-route",     label: "Just enough to complete the trip", sub: "Fastest stop, minimum charge" },
                      { mode: "charge_to_80"   as const, icon: "ti-battery-2", label: "Charge to 80%",                   sub: "Recommended — fast & efficient" },
                      { mode: "charge_to_100"  as const, icon: "ti-battery-4", label: "Charge to 100%",                  sub: "Full charge, slower above 80%" },
                    ].map(({ mode, icon, label, sub }) => (
                      <button key={mode} onClick={() => handleChargingMode(mode)} style={{
                        background: "var(--bg)", border: "0.5px solid var(--border)",
                        borderRadius: 8, padding: "10px 14px", cursor: "pointer",
                        display: "flex", alignItems: "center", gap: 10, textAlign: "left",
                        width: "100%", transition: "border-color 0.15s",
                      }}
                        onMouseEnter={e => (e.currentTarget.style.borderColor = "var(--primary)")}
                        onMouseLeave={e => (e.currentTarget.style.borderColor = "var(--border)")}
                      >
                        <i className={`ti ${icon}`} style={{ fontSize: 18, color: "var(--accent)", flexShrink: 0 }} />
                        <div>
                          <div style={{ fontSize: 13, color: "var(--text)", fontWeight: 500 }}>{label}</div>
                          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{sub}</div>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* Loading */}
            {loading && (
              <div style={{ maxWidth: "88%" }}>
                <div style={{
                  background: "var(--white)", border: "0.5px solid var(--border)",
                  borderLeft: "3px solid var(--primary)",
                  borderRadius: "0 12px 12px 12px", padding: "12px 14px",
                  fontSize: 13, color: "var(--muted)",
                }}>
                  {loadingMessage}
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input bar */}
          <div style={{
            padding: "12px 20px", borderTop: "0.5px solid var(--border)",
            background: "var(--bg)", display: "flex", gap: 10, alignItems: "center",
          }}>
            <input
              style={{
                flex: 1, background: "var(--bg)", border: "0.5px solid var(--border)",
                borderRadius: 8, padding: "10px 14px", fontSize: 13, color: "var(--text)", outline: "none",
              }}
              placeholder={
                transcribing ? "Transcribing..." :
                recording    ? "Recording... click mic to stop" :
                "How can I assist you with your trip planning today?"
              }
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleSend()}
              onFocus={e => (e.target.style.borderColor = "var(--primary)")}
              onBlur={e  => (e.target.style.borderColor = "var(--border)")}
              disabled={awaitingChargingMode || recording || transcribing}
            />

            {/* Mic button */}
            <button
              onClick={handleRecord}
              disabled={loading || awaitingChargingMode || transcribing}
              title={recording ? "Stop recording" : "Record voice message"}
              style={{
                background: recording ? "#E24B4A" : "var(--bg)",
                border: `0.5px solid ${recording ? "#E24B4A" : "var(--border)"}`,
                borderRadius: 8, padding: "10px 13px",
                color: recording ? "#fff" : "var(--muted)",
                fontSize: 14, cursor: "pointer",
                opacity: (loading || awaitingChargingMode || transcribing) ? 0.5 : 1,
                transition: "all 0.2s",
                flexShrink: 0,
              }}>
              <i
                className={`ti ${transcribing ? "ti-loader-2 spin" : recording ? "ti-player-stop" : "ti-microphone"}`}
              />
            </button>

            {/* Send button */}
            <button
              onClick={() => handleSend()}
              disabled={loading || awaitingChargingMode}
              style={{
                background: "var(--primary)", border: "none", borderRadius: 8,
                padding: "10px 16px", color: "#F5F2EA", fontSize: 13, cursor: "pointer",
                opacity: (loading || awaitingChargingMode) ? 0.6 : 1,
                flexShrink: 0,
              }}>
              <i className="ti ti-send" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════
   STATION CARD — rendered from mapData directly
══════════════════════════════════════════════════════ */
function StationCard({ station }: { station: StationMapData }) {
  const tags: { label: string; amber: boolean }[] = [];
  if (station.available)        tags.push({ label: "Available", amber: false });
  if (station.has_fast_charge)  tags.push({ label: "Fast charge", amber: false });
  if (station.needs_membership) tags.push({ label: "Members only", amber: true });
  else                          tags.push({ label: "Public", amber: false });

  return (
    <div style={{ background: "var(--white)", border: "0.5px solid var(--border)", borderRadius: 10, overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        background: "var(--primary)", padding: "10px 14px",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <div style={{ color: "#F5F2EA", fontSize: 13, fontWeight: 500, display: "flex", alignItems: "center", gap: 6 }}>
          <i className="ti ti-bolt" />
          {station.station_name}
        </div>
        {station.score && (
          <span style={{
            background: "var(--accent)", color: "var(--primary)",
            fontSize: 11, fontWeight: 500, borderRadius: 20, padding: "2px 8px",
          }}>{station.score}/100</span>
        )}
      </div>

      {/* Details grid */}
      <div style={{ padding: "10px 14px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        {[
          { icon: "ti-building", label: "Operator",    val: station.operator },
          { icon: "ti-clock",    label: "Charge time", val: `~${station.charge_time} min to ${station.target_battery}%` },
          { icon: "ti-plug",     label: "Connectors",  val: station.connectors },
          { icon: "ti-route",    label: "Detour",      val: `${station.detour_km} km from route` },
          { icon: "ti-currency-pound", label: "Rate",  val: `${station.rate_egp_kwh} EGP/kWh` },
        ].filter(r => r.val).map(r => (
          <div key={r.label} style={{ fontSize: 11, color: "#5F5E5A", display: "flex", alignItems: "center", gap: 5 }}>
            <i className={`ti ${r.icon}`} />
            {r.label}
            <span style={{ color: "var(--text)", fontWeight: 500, marginLeft: "auto" }}>{r.val}</span>
          </div>
        ))}
      </div>

      {/* Tags */}
      {tags.length > 0 && (
        <div style={{ padding: "8px 14px", borderTop: "0.5px solid var(--border)", display: "flex", gap: 6, flexWrap: "wrap" }}>
          {tags.map(t => (
            <span key={t.label} style={{
              fontSize: 10, borderRadius: 20, padding: "3px 8px",
              background: t.amber ? "var(--amber-tag)" : "var(--green-tag)",
              color:      t.amber ? "var(--amber-text)" : "var(--green-text)",
            }}>{t.label}</span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ══════════════════════════════════════════════════════
   RATE THE EXPERIENCE
══════════════════════════════════════════════════════ */
type RateStep = "closed" | "pick_station" | "liked_tags" | "avoided_tags" | "preference_tags" | "done";

function RateExperience({ stations, onSubmit }: {
  stations: StationMapData[];
  onSubmit: (stationId: string, stationName: string, type: string, tag: string) => Promise<void>;
}) {
  const [step, setStep]             = useState<RateStep>("closed");
  const [picked, setPicked]         = useState<StationMapData | null>(null);
  const [submitted, setSubmitted]   = useState(false);

  if (submitted) {
    return (
      <div style={{ fontSize: 11, color: "var(--muted)", display: "flex", alignItems: "center", gap: 5, marginTop: 4 }}>
        <i className="ti ti-check" style={{ color: "var(--status-dot)" }} />
        Thanks for your feedback
      </div>
    );
  }

  async function handleTag(type: string, tag: string) {
    if (!picked) return;
    await onSubmit(picked.station_id, picked.station_name, type, tag);
    setSubmitted(true);
  }

  // Closed
  if (step === "closed") {
    return (
      <button onClick={() => setStep("pick_station")} style={{
        background: "none", border: "0.5px solid var(--border)",
        borderRadius: 6, padding: "5px 12px", fontSize: 11,
        color: "var(--muted)", cursor: "pointer",
        display: "flex", alignItems: "center", gap: 5, marginTop: 4,
      }}>
        <i className="ti ti-star" style={{ fontSize: 12 }} />
        Rate the experience
      </button>
    );
  }

  // Step 1 — pick which station
  if (step === "pick_station") {
    return (
      <div style={{ marginTop: 8 }}>
        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6 }}>Which station did you use?</div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {stations.map((s, i) => (
            <TagBtn key={s.station_id} label={`${s.station_name} #${i+1}`}
              onClick={() => { setPicked(s); setStep("initial" as any); }} />
          ))}
          <TagBtn label="I didn't charge" muted onClick={() => setStep("closed")} />
        </div>
      </div>
    );
  }

  // Step 2 — what kind of feedback (reuse "initial" step key)
  if (step === ("initial" as any)) {
    return (
      <div style={{ marginTop: 8 }}>
        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6 }}>
          Feedback for <strong>{picked?.station_name}</strong>:
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <TagBtn label="I liked this station" green onClick={() => setStep("liked_tags")} />
          <TagBtn label="Issue with station"        onClick={() => setStep("avoided_tags")} />
          <TagBtn label="Not right for this trip"   onClick={() => setStep("preference_tags")} />
          <TagBtn label="Back" muted                onClick={() => setStep("pick_station")} />
        </div>
      </div>
    );
  }

  // Liked tags
  if (step === "liked_tags") {
    return (
      <div style={{ marginTop: 8 }}>
        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6 }}>What did you like?</div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {["Great experience", "Would visit again", "Convenient location"].map(tag => (
            <TagBtn key={tag} label={tag} green onClick={() => handleTag("liked", tag)} />
          ))}
          <TagBtn label="Back" muted onClick={() => setStep("initial" as any)} />
        </div>
      </div>
    );
  }

  // Avoided tags
  if (step === "avoided_tags") {
    return (
      <div style={{ marginTop: 8 }}>
        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6 }}>What was the issue?</div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {["Needs membership", "Bad experience", "Charger was broken", "Too expensive operator"].map(tag => (
            <TagBtn key={tag} label={tag} onClick={() => handleTag("avoided", tag)} />
          ))}
          <TagBtn label="Back" muted onClick={() => setStep("initial" as any)} />
        </div>
      </div>
    );
  }

  // Preference tags
  if (step === "preference_tags") {
    return (
      <div style={{ marginTop: 8 }}>
        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6 }}>What would work better next time?</div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {["Too far out of my way", "Need a faster charger", "Want a cheaper option", "Need public access"].map(tag => (
            <TagBtn key={tag} label={tag} onClick={() => handleTag("preference", tag.toLowerCase())} />
          ))}
          <TagBtn label="Back" muted onClick={() => setStep("initial" as any)} />
        </div>
      </div>
    );
  }

  return null;
}

function TagBtn({ label, onClick, green, muted }: {
  label: string; onClick: () => void; green?: boolean; muted?: boolean;
}) {
  return (
    <button onClick={onClick} style={{
      background:   green ? "var(--green-tag)" : muted ? "none" : "var(--bg)",
      border:       `0.5px solid ${green ? "var(--green-text)" : "var(--border)"}`,
      borderRadius: 20, padding: "4px 10px", fontSize: 11,
      color:        green ? "var(--green-text)" : muted ? "var(--muted)" : "var(--text)",
      cursor:       "pointer", whiteSpace: "nowrap" as const,
    }}>
      {label}
    </button>
  );
}

/* ══════════════════════════════════════════════════════
   MAP PREVIEW
══════════════════════════════════════════════════════ */
const MAPS_KEY = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY || "";
const mapStyle = [
  { featureType: "poi",           stylers: [{ visibility: "off" }] },
  { featureType: "transit",       stylers: [{ visibility: "off" }] },
  { elementType: "geometry",      stylers: [{ color: "#f5f2ea" }] },
  { featureType: "road",          elementType: "geometry", stylers: [{ color: "#ffffff" }] },
  { featureType: "road.arterial", elementType: "geometry", stylers: [{ color: "#ede9df" }] },
  { featureType: "water",         elementType: "geometry", stylers: [{ color: "#c9e8f5" }] },
];

function MapPreview({ station, onClose }: { station: StationMapData; onClose: () => void }) {
  const { isLoaded } = useJsApiLoader({ googleMapsApiKey: MAPS_KEY });

  const originPos  = { lat: station.origin_lat,  lng: station.origin_lng };
  const stationPos = { lat: station.station_lat, lng: station.station_lng };
  const destPos    = { lat: station.dest_lat,    lng: station.dest_lng };

  return (
    <div style={{ marginTop: 8, background: "var(--white)", border: "0.5px solid var(--border)", borderRadius: 10, overflow: "hidden" }}>
      <div style={{
        background: "var(--bg)", padding: "8px 14px", borderBottom: "0.5px solid var(--border)",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <div style={{ fontSize: 12, color: "var(--text)", fontWeight: 500, display: "flex", alignItems: "center", gap: 6 }}>
          <i className="ti ti-map" /> {station.origin_name} → {station.station_name} → {station.dest_name}
        </div>
        <button onClick={onClose} style={{ background: "none", border: "none", fontSize: 12, color: "var(--muted)", cursor: "pointer" }}>
          ✕ Close
        </button>
      </div>

      {!isLoaded ? (
        <div style={{ height: 260, display: "flex", alignItems: "center", justifyContent: "center", background: "#e8e4d9", fontSize: 13, color: "var(--muted)" }}>
          Loading map...
        </div>
      ) : (
        <GoogleMap
          mapContainerStyle={{ width: "100%", height: 260 }}
          center={stationPos} zoom={13}
          options={{ disableDefaultUI: true, zoomControl: true, styles: mapStyle }}
        >
          <Polyline path={[originPos, stationPos, destPos]} options={{ strokeColor: "#1E3A2F", strokeOpacity: 0.8, strokeWeight: 3 }} />
          <Marker position={originPos} icon={{
            url: `data:image/svg+xml;utf-8,<svg xmlns='http://www.w3.org/2000/svg' width='32' height='40' viewBox='0 0 32 40'><path d='M16 0C7.16 0 0 7.16 0 16c0 12 16 24 16 24s16-12 16-24C32 7.16 24.84 0 16 0z' fill='%231E3A2F'/><text x='16' y='21' text-anchor='middle' font-size='13' font-weight='bold' fill='white' font-family='sans-serif'>A</text></svg>`,
            scaledSize: { width: 32, height: 40 } as any,
          }} />
          <Marker position={stationPos} icon={{
            url: `data:image/svg+xml;utf-8,<svg xmlns='http://www.w3.org/2000/svg' width='36' height='46' viewBox='0 0 36 46'><path d='M18 0C8.06 0 0 8.06 0 18c0 13.5 18 28 18 28s18-14.5 18-28C36 8.06 27.94 0 18 0z' fill='%233B6D11'/><text x='18' y='24' text-anchor='middle' font-size='16' fill='white' font-family='sans-serif'>⚡</text></svg>`,
            scaledSize: { width: 36, height: 46 } as any,
          }} />
          <Marker position={destPos} icon={{
            url: `data:image/svg+xml;utf-8,<svg xmlns='http://www.w3.org/2000/svg' width='32' height='40' viewBox='0 0 32 40'><path d='M16 0C7.16 0 0 7.16 0 16c0 12 16 24 16 24s16-12 16-24C32 7.16 24.84 0 16 0z' fill='%23C9A84C'/><text x='16' y='21' text-anchor='middle' font-size='13' font-weight='bold' fill='%231E3A2F' font-family='sans-serif'>B</text></svg>`,
            scaledSize: { width: 32, height: 40 } as any,
          }} />
        </GoogleMap>
      )}

      <div style={{ padding: "8px 14px", display: "flex", gap: 12, borderTop: "0.5px solid var(--border)", background: "var(--white)" }}>
        {[{ color: "#1E3A2F", label: "Origin" }, { color: "#3B6D11", label: "Charging station" }, { color: "#C9A84C", label: "Destination" }].map(l => (
          <div key={l.label} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10, color: "#5F5E5A" }}>
            <div style={{ width: 10, height: 10, borderRadius: "50%", background: l.color }} />
            {l.label}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════
   SMALL COMPONENTS
══════════════════════════════════════════════════════ */
function SpecItem({ icon, label }: { icon: string; label: string }) {
  return (
    <div style={{ fontSize: 11, color: "var(--muted2)", display: "flex", alignItems: "center", gap: 5 }}>
      <i className={`ti ${icon}`} style={{ fontSize: 12, color: "var(--accent)", flexShrink: 0 }} />
      {label}
    </div>
  );
}

function StatBox({ num, label }: { num: number; label: string }) {
  return (
    <div style={{ flex: 1, background: "var(--primary2)", borderRadius: 8, padding: 8, textAlign: "center" }}>
      <div style={{ color: "#F5F2EA", fontSize: 16, fontWeight: 500 }}>{num}</div>
      <div style={{ color: "#6B8F7E", fontSize: 10, marginTop: 2 }}>{label}</div>
    </div>
  );
}

function SidebarBtn({ icon, label, onClick }: { icon: string; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} style={{
      background: "var(--primary2)", border: "none", borderRadius: 8,
      padding: "8px 12px", color: "var(--muted2)", fontSize: 12,
      width: "100%", textAlign: "left", display: "flex", alignItems: "center", gap: 8, cursor: "pointer",
    }}>
      <i className={`ti ${icon}`} style={{ fontSize: 14 }} />
      {label}
    </button>
  );
}

const sidebarLabelStyle: React.CSSProperties = {
  color: "#6B8F7E", fontSize: 10, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6,
};