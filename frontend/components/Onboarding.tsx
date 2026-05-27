"use client";

import { useState, useEffect } from "react";
import { login, register, updateProfile, getBrands, getModels, getSpecs } from "../lib/api";

interface Props {
  onComplete: (profile: any) => void;
  existingProfile: any;
  initialTab?: "new" | "returning";
}

const PRICE_OPTIONS = [
  { value: "low cost",   label: "Low cost",   sub: "AC charging (3.97 EGP/kWh)",   icon: "ti-currency-dollar" },
  { value: "any price",  label: "Any price",  sub: "AC or DC (up to 7.67 EGP/kWh)",      icon: "ti-wallet" },
];

const AC_OPTIONS = [
  { value: "always",    label: "Always",    icon: "ti-wind" },
  { value: "sometimes", label: "Sometimes", icon: "ti-wind" },
  { value: "never",     label: "Never",     icon: "ti-wind-off" },
];


export default function Onboarding({ onComplete, existingProfile, initialTab }: Props) {
  const isUpdate = !!existingProfile;
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  const [step, setStep] = useState(existingProfile ? 2 : 1);
  const [tab, setTab]   = useState<"new" | "returning">(initialTab || "new");

  // Step 1 — shared fields
  const [name, setName]                   = useState(existingProfile?.name || "");
  const [pin, setPin]                     = useState(existingProfile?.pin || "");
  const [showPin, setShowPin]             = useState(false);
  const [showPinReturn, setShowPinReturn] = useState(false);
  const [error, setError]                 = useState("");

  // Step 2
  const [brands, setBrands] = useState<string[]>([]);
  const [brand, setBrand]   = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel]   = useState(existingProfile?.car_model || "");
  const [specs, setSpecs]   = useState<any>(null);

  // Step 3
  const [price, setPrice]         = useState(existingProfile?.price_sensitivity || "low cost");
  const [ac, setAc]               = useState(existingProfile?.ac_usage || "sometimes");
  const [detour, setDetour]       = useState(existingProfile?.max_detour_km || 5);
  const [threshold, setThreshold] = useState(existingProfile?.min_battery_threshold || 15);

  // Step 4
  const [homeLoc, setHomeLoc]   = useState(
    existingProfile?.saved_destinations?.find((d: any) => d.label === "Home")?.name || ""
  );
  const [workLoc, setWorkLoc]   = useState(
    existingProfile?.saved_destinations?.find((d: any) => d.label === "Work")?.name || ""
  );
  const [otherLoc, setOtherLoc] = useState(
    existingProfile?.saved_destinations?.find((d: any) => d.label === "Other")?.name || ""
  );
  
  useEffect(() => {
    if (step === 2 && brands.length === 0) {
      getBrands().then(b => { setBrands(b); setBrand(""); });
    }
  }, [step]);

  useEffect(() => {
    if (brand) {
      getModels(brand).then(m => { setModels(m); setModel(""); });
    }
  }, [brand]);

  useEffect(() => {
    if (model) {
      getSpecs(model).then(s => setSpecs(s));
    }
  }, [model]);

  async function handleContinue() {
    setError("");
    if (tab === "new") {
      if (!name.trim())            { setError("Please enter your name."); return; }
      if (!/^\d{4}$/.test(pin))   { setError("PIN must be exactly 4 digits."); return; }
      setStep(2);
    } else {
      const res = await login(name.trim(), pin);
      if (res.ok) { onComplete(res.profile); }
      else        { setError(res.error || "Name or PIN is incorrect."); }
    }
  }

async function handleFinish() {
  const dests: any[] = [];
  if (homeLoc.trim())  dests.push({ label: "Home",  name: homeLoc.trim() });
  if (workLoc.trim())  dests.push({ label: "Work",  name: workLoc.trim() });
  if (otherLoc.trim()) dests.push({ label: "Other", name: otherLoc.trim() });

  const payload = {
    name: name.trim(), pin,
    car_model: model,
    price_sensitivity: price,
    ac_usage: ac,
    min_battery_threshold: threshold,
    max_detour_km: detour,
    saved_destinations: dests,
  };

  const res = isUpdate
    ? await updateProfile(payload)
    : await register(payload);

  if (res.ok) { onComplete(res.profile); }
  else        { setError(res.error || "Something went wrong."); }
}

  const stepLabels = ["Account", "Your car", "Preferences", "Destinations"];

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg)", display: "flex", flexDirection: "column" }}>

      {/* ── Topbar ── */}
      <div style={{
        background: "var(--primary)", padding: "10px 24px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--accent)" }} />
          <span style={{ color: "#F5F2EA", fontSize: 14, fontWeight: 500 }}>SEVA — Smart EV Assistant</span>
        </div>
        <span style={{ fontSize: 11, color: "var(--muted3)" }}>Setup · Step {step} of 4</span>
      </div>

      {/* ── Progress bar ── */}
      <div style={{ padding: isMobile ? "16px 16px 0" : "20px 40px 0" }}>
        <div style={{ display: "flex", alignItems: "center", marginBottom: 6 }}>
          {[1, 2, 3, 4].map((n) => (
            <div key={n} style={{ display: "flex", alignItems: "center", flex: n < 4 ? 1 : "none" }}>
              <div style={{
                width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 12, fontWeight: 500,
                background: n < step ? "var(--primary)" : n === step ? "var(--accent)" : "var(--step-inact)",
                color: n < step ? "#F5F2EA" : n === step ? "var(--primary)" : "var(--muted2)",
              }}>
                {n < step ? <i className="ti ti-check" style={{ fontSize: 13 }} /> : n}
              </div>
              {n < 4 && (
                <div style={{
                  flex: 1, height: 2, margin: "0 4px",
                  background: n < step ? "var(--primary)" : "var(--step-inact)",
                }} />
              )}
            </div>
          ))}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", padding: "0 4px" }}>
          {stepLabels.map((lbl, i) => (
            <span key={lbl} style={{
              fontSize: 10, textAlign: "center", width: 72, marginLeft: i === 0 ? 0 : -16,
              color: i + 1 === step ? "var(--primary)" : "var(--muted2)",
              fontWeight: i + 1 === step ? 500 : 400,
            }}>{lbl}</span>
          ))}
        </div>
      </div>

      {/* ── Content ── */}
      <div style={{ display: "flex", gap: 32, padding: isMobile ? "16px 16px" : "20px 40px", flex: 1 }}>
        <div style={{ flex: 1 }}>

          {/* ════════════ STEP 1 — Account ════════════ */}
          {step === 1 && (
            <div style={cardStyle}>
              {/* Icon */}
              <div style={{
                width: 56, height: 56, background: "var(--primary)", borderRadius: 14,
                display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 16,
              }}>
                <i className="ti ti-bolt" style={{ fontSize: 28, color: "var(--accent)" }} />
              </div>

              <div style={titleStyle}>Welcome to SEVA</div>
              <div style={subStyle}>Create your profile to get personalized charging recommendations</div>

              {/* Tabs */}
              <div style={{ display: "flex", borderBottom: "0.5px solid var(--border)", marginBottom: 20 }}>
                {(["new", "returning"] as const).map(t => (
                  <button key={t} onClick={() => { setTab(t); setError(""); setPin(""); }} style={{
                    padding: "8px 16px", fontSize: 12, background: "none", border: "none",
                    borderBottom: tab === t ? "2px solid var(--primary)" : "2px solid transparent",
                    color: tab === t ? "var(--primary)" : "var(--muted)",
                    fontWeight: tab === t ? 500 : 400, marginBottom: -1, cursor: "pointer",
                  }}>
                    {t === "new" ? "New user" : "Returning user"}
                  </button>
                ))}
              </div>

              {/* Name — shared */}
              <Field label="Your name">
                <input
                  style={inputStyle} type="text" placeholder="Enter your name"
                  value={name} onChange={e => setName(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && handleContinue()}
                />
              </Field>

              {/* PIN — different label + eye toggle per tab */}
              {tab === "new" ? (
                <Field label="Choose a 4-digit PIN">
                  <PinInput
                    value={pin} onChange={setPin}
                    show={showPin} onToggle={() => setShowPin(p => !p)}
                    placeholder="Choose a 4-digit PIN"
                    onEnter={handleContinue}
                  />
                </Field>
              ) : (
                <Field label="Your PIN">
                  <PinInput
                    value={pin} onChange={setPin}
                    show={showPinReturn} onToggle={() => setShowPinReturn(p => !p)}
                    placeholder="Enter your PIN"
                    onEnter={handleContinue}
                  />
                </Field>
              )}

              {error && <div style={{ color: "#E24B4A", fontSize: 12, marginBottom: 12 }}>{error}</div>}

              <button style={btnPrimaryStyle} onClick={handleContinue}>
                {tab === "new" ? "Continue →" : "Log in →"}
              </button>
            </div>
          )}

          {/* ════════════ STEP 2 — Your car ════════════ */}
          {step === 2 && (
            <div style={cardStyle}>
              <div style={titleStyle}>What car do you drive?</div>
              <div style={subStyle}>We use this to check connector compatibility and estimate charge times</div>

              <Field label="Brand">
                <select style={inputStyle} value={brand} onChange={e => setBrand(e.target.value)}>
                  <option value="" disabled>Select a brand</option>
                  {brands.map(b => <option key={b} value={b}>{b}</option>)}
                </select>
              </Field>

              <Field label="Model">
                <select style={inputStyle} value={model} onChange={e => setModel(e.target.value)}>
                  <option value="" disabled>Select a model</option>
                  {models.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </Field>

              {/* Spec preview card */}
              {specs && (
                <div style={{
                  background: "var(--bg)", borderRadius: 8,
                  padding: "12px 14px", marginTop: 4, marginBottom: 8,
                  border: "0.5px solid var(--border)",
                }}>
                  <div style={{ fontSize: 11, color: "#5F5E5A", marginBottom: 8, fontWeight: 500 }}>
                    {brand} {model}
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                    <SpecRow icon="ti-battery"  label="Battery" value={`${specs.battery_kwh} kWh`} />
                    <SpecRow icon="ti-road"     label="Range"   value={`${specs.range_km} km`} />
                    <SpecRow icon="ti-plug"     label="AC"      value={`${specs.ac_connector} · ${specs.max_ac_kw}kW`} />
                    <SpecRow icon="ti-bolt"     label="DC"      value={`${specs.dc_connector} · ${specs.max_dc_kw}kW`} />
                  </div>
                </div>
              )}

              {error && <div style={{ color: "#E24B4A", fontSize: 12, marginBottom: 8 }}>{error}</div>}

              <BtnRow
                onBack={() => { setError(""); setStep(1); }}
                onNext={() => {
                  if (!brand || !model) { setError("Please select a brand and model."); return; }
                  setError("");
                  setStep(3);
                }}
              />
            </div>
          )}

          {/* ════════════ STEP 3 — Preferences ════════════ */}
          {step === 3 && (
            <div style={cardStyle}>
              <div style={titleStyle}>Your preferences</div>
              <div style={subStyle}>These shape how SEVA ranks charging stations for you</div>

              <Field label="Price sensitivity">
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  {PRICE_OPTIONS.map(opt => (
                    <OptionCard key={opt.value} icon={opt.icon} label={opt.label} sub={opt.sub}
                      active={price === opt.value} onClick={() => setPrice(opt.value)} />
                  ))}
                </div>
              </Field>

              <Field label="Air conditioning usage">
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                  {AC_OPTIONS.map(opt => (
                    <OptionCard key={opt.value} icon={opt.icon} label={opt.label}
                      active={ac === opt.value} onClick={() => setAc(opt.value)} />
                  ))}
                </div>
              </Field>

              <Field label={<>Max detour distance — <strong style={{ color: "var(--primary)" }}>{detour} km</strong></>}>
                <input type="range" min={1} max={20} value={detour}
                  onChange={e => setDetour(+e.target.value)}
                  style={{ width: "100%", accentColor: "var(--primary)", marginTop: 4 }} />
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                  <span>1 km</span><span>20 km</span>
                </div>
              </Field>

              <Field label={<>Minimum battery threshold — <strong style={{ color: "var(--primary)" }}>{threshold}%</strong></>}>
                <input type="range" min={5} max={30} value={threshold}
                  onChange={e => setThreshold(+e.target.value)}
                  style={{ width: "100%", accentColor: "var(--primary)", marginTop: 4 }} />
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                  <span>5%</span><span>30%</span>
                </div>
              </Field>

              <BtnRow onBack={() => setStep(2)} onNext={() => setStep(4)} />
            </div>
          )}

          {/* ════════════ STEP 4 — Destinations ════════════ */}
          {step === 4 && (
            <div style={cardStyle}>
              <div style={titleStyle}>Saved destinations</div>
              <div style={subStyle}>Optional — helps SEVA give bonus score to stations near places you visit often</div>

              <DestRow icon="ti-home"      placeholder="Home location"  value={homeLoc}  onChange={setHomeLoc}  onEnter={handleFinish} />
              <DestRow icon="ti-briefcase" placeholder="Work location"  value={workLoc}  onChange={setWorkLoc}  onEnter={handleFinish} />
              <DestRow icon="ti-map-pin"   placeholder="Other location" value={otherLoc} onChange={setOtherLoc} onEnter={handleFinish} />

              {error && <div style={{ color: "#E24B4A", fontSize: 12, marginTop: 8 }}>{error}</div>}

              <div style={{ display: "flex", gap: 10, marginTop: 24 }}>
                <button style={btnBackStyle} onClick={() => setStep(3)}>← Back</button>
                <button
                  style={{ ...btnPrimaryStyle, flex: 2, background: "var(--accent)", color: "var(--primary)" }}
                  onClick={handleFinish}>
                  {isUpdate ? "Save changes" : "Start using SEVA ⚡"}
                </button>
              </div>
            </div>
          )}

        </div>

        {/* ── Step panel (right side) — hidden on mobile ── */}
        <div style={{ width: 160, display: isMobile ? "none" : "flex", flexDirection: "column", gap: 10, paddingTop: 4 }}>
          <div style={{ fontSize: 10, color: "var(--muted2)", textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>
            Steps
          </div>
          {[
            { n: 1, label: "Account",      icon: "ti-user" },
            { n: 2, label: "Your car",     icon: "ti-car" },
            { n: 3, label: "Preferences",  icon: "ti-sliders" },
            { n: 4, label: "Destinations", icon: "ti-map-pin" },
          ].map(({ n, label, icon }) => {
            const done   = n < step;
            const active = n === step;
            return (
              <div key={n} style={{
                display: "flex", alignItems: "center", gap: 8,
                padding: "8px 10px", borderRadius: 8,
                background: done ? "var(--primary)" : active ? "var(--accent)" : "#F5F2EA",
                border: done || active ? "none" : "0.5px solid var(--border)",
              }}>
                <i className={`ti ${done ? "ti-check" : icon}`} style={{
                  fontSize: 14,
                  color: done ? "var(--accent)" : active ? "var(--primary)" : "var(--muted2)",
                }} />
                <span style={{
                  fontSize: 12,
                  color: done ? "#F5F2EA" : active ? "var(--primary)" : "var(--muted2)",
                  fontWeight: active ? 500 : 400,
                }}>{label}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════
   SUB-COMPONENTS
══════════════════════════════════════════════════════ */

function PinInput({ value, onChange, show, onToggle, placeholder, onEnter }: {
  value: string; onChange: (v: string) => void;
  show: boolean; onToggle: () => void; placeholder: string;
  onEnter?: () => void;
}) {
  return (
    <div style={{ position: "relative" }}>
      <input
        style={{ ...inputStyle, paddingRight: 40 }}
        type={show ? "text" : "password"}
        maxLength={4}
        placeholder={placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
        onKeyDown={e => e.key === "Enter" && onEnter?.()}
      />
      <button
        type="button"
        onClick={onToggle}
        style={{
          position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
          background: "none", border: "none", cursor: "pointer",
          color: "var(--muted)", fontSize: 16, padding: 0,
          display: "flex", alignItems: "center",
        }}>
        <i className={`ti ${show ? "ti-eye-off" : "ti-eye"}`} />
      </button>
    </div>
  );
}

function Field({ label, children }: { label: any; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <label style={{ display: "block", fontSize: 12, color: "#5F5E5A", marginBottom: 6, fontWeight: 500 }}>
        {label}
      </label>
      {children}
    </div>
  );
}

function SpecRow({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <div style={{ fontSize: 11, color: "#888780", display: "flex", alignItems: "center", gap: 4 }}>
      <i className={`ti ${icon}`} style={{ fontSize: 13 }} />
      {label}: <span style={{ color: "var(--text)", fontWeight: 500 }}>{value}</span>
    </div>
  );
}

function OptionCard({ icon, label, sub, active, onClick }: {
  icon: string; label: string; sub?: string; active: boolean; onClick: () => void;
}) {
  return (
    <div onClick={onClick} style={{
      border: active ? "2px solid var(--primary)" : "0.5px solid var(--border)",
      borderRadius: 8, padding: "12px 8px", textAlign: "center", cursor: "pointer",
      background: active ? "var(--white)" : "var(--bg)",
    }}>
      <i className={`ti ${icon}`} style={{ fontSize: 18, color: "var(--primary)", display: "block", marginBottom: 4 }} />
      <div style={{ fontSize: 11, color: "var(--text)", fontWeight: 500 }}>{label}</div>
      {sub && <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function DestRow({ icon, placeholder, value, onChange, onEnter }: {
  icon: string; placeholder: string; value: string;
  onChange: (v: string) => void; onEnter?: () => void;
}) {
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10 }}>
      <div style={{
        width: 32, height: 32, borderRadius: 8, background: "var(--primary)",
        display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
      }}>
        <i className={`ti ${icon}`} style={{ fontSize: 16, color: "#F5F2EA" }} />
      </div>
      <input
        style={{
          flex: 1, background: "var(--bg)", border: "0.5px solid var(--border)",
          borderRadius: 8, padding: "9px 12px", fontSize: 13, color: "var(--text)", outline: "none",
        }}
        placeholder={placeholder} value={value}
        onChange={e => onChange(e.target.value)}
        onKeyDown={e => e.key === "Enter" && onEnter?.()}
      />
    </div>
  );
}

function BtnRow({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  return (
    <div style={{ display: "flex", gap: 10, marginTop: 24 }}>
      <button style={btnBackStyle} onClick={onBack}>← Back</button>
      <button style={{ ...btnPrimaryStyle, flex: 2 }} onClick={onNext}>Continue →</button>
    </div>
  );
}
const cardStyle: React.CSSProperties = {
  background: "#fff", border: "0.5px solid var(--border)",
  borderRadius: 12, padding: "28px 32px", width: "100%",
};
const titleStyle: React.CSSProperties = {
  fontSize: 17, fontWeight: 500, color: "var(--primary)", marginBottom: 4,
};
const subStyle: React.CSSProperties = {
  fontSize: 12, color: "var(--muted)", marginBottom: 24,
};
const inputStyle: React.CSSProperties = {
  width: "100%", background: "var(--bg)", border: "0.5px solid var(--border)",
  borderRadius: 8, padding: "10px 12px", fontSize: 13, color: "var(--text)", outline: "none",
};
const btnPrimaryStyle: React.CSSProperties = {
  flex: 1, background: "var(--primary)", border: "none", borderRadius: 8,
  padding: "12px 20px", fontSize: 13, color: "#F5F2EA", fontWeight: 500, cursor: "pointer", width: "100%",
};
const btnBackStyle: React.CSSProperties = {
  flex: 1, background: "none", border: "0.5px solid var(--border)",
  borderRadius: 8, padding: "12px 20px", fontSize: 13, color: "#5F5E5A", cursor: "pointer",
};