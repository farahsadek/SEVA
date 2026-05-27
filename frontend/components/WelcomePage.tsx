"use client";

import { useState, useEffect } from "react";

interface Props {
  onGetStarted: () => void;
  onLogin: () => void;
}

export default function WelcomePage({ onGetStarted, onLogin }: Props) {
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  return (
    <div style={{ background: "var(--bg)", minHeight: "100vh", display: "flex", flexDirection: "column" }}>

      {/* ── Topbar ── */}
      <div style={{
        background: "var(--primary)", padding: "12px 32px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--accent)" }} />
          <span style={{ color: "#F5F2EA", fontSize: 15, fontWeight: 500 }}>SEVA — Smart EV Assistant</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button onClick={onLogin} style={{
            background: "none", border: "1px solid var(--accent)", borderRadius: 8,
            padding: "7px 20px", color: "var(--accent)", fontSize: 13,
            fontWeight: 500, cursor: "pointer",
          }}>
            Log in
          </button>
          <button onClick={onGetStarted} style={{
            background: "var(--accent)", border: "none", borderRadius: 8,
            padding: "7px 20px", color: "var(--primary)", fontSize: 13,
            fontWeight: 500, cursor: "pointer",
          }}>
            Sign up
          </button>
        </div>
      </div>

      {/* ── Hero ── */}
      <div style={{
        display: "flex", flexDirection: "column", alignItems: "center",
        justifyContent: "center", textAlign: "center",
        padding: isMobile ? "32px 20px 20px" : "56px 32px 32px",
      }}>
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          background: "var(--green-tag)", color: "var(--primary)", borderRadius: 20,
          padding: "4px 14px", fontSize: 12, fontWeight: 500,
          border: "0.5px solid var(--border)", marginBottom: 24,
        }}>
          <i className="ti ti-bolt" style={{ fontSize: 13 }} />
          Built for Egyptian EV drivers
        </div>

        <h1 style={{
          fontSize: isMobile ? 26 : 38, fontWeight: 600, color: "var(--primary)",
          lineHeight: 1.2, marginBottom: 16, maxWidth: 540,
        }}>
          Find the right charger, on your route
        </h1>

        <p style={{
          fontSize: 15, color: "var(--muted)", lineHeight: 1.7,
          maxWidth: 460, marginBottom: 40,
        }}>
          SEVA recommends charging stations based on your car, your route,
          and your preferences — not just what's nearest.
        </p>

        <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap", flexDirection: isMobile ? "column" : "row", alignItems: "center" }}>
          <button onClick={onGetStarted} style={{
            background: "var(--primary)", color: "#F5F2EA", border: "none",
            borderRadius: 8, padding: "13px 32px", fontSize: 14,
            fontWeight: 500, cursor: "pointer",
          }}>
            Get started — it's free
          </button>
          <button onClick={onLogin} style={{
            background: "none", color: "var(--primary)",
            border: "1.5px solid var(--primary)", borderRadius: 8,
            padding: "13px 32px", fontSize: 14, fontWeight: 500, cursor: "pointer",
          }}>
            Log in to your account
          </button>
        </div>
      </div>

      {/* ── Feature cards ── */}
      <div style={{
        display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(3, 1fr)",
        gap: 16, padding: isMobile ? "16px 20px 20px" : "16px 32px 32px",
        maxWidth: 880, margin: "0 auto", width: "100%",
      }}>
        {[
          { icon: "ti-route",                  title: "Route-aware",        desc: "Stations are scored based on how far they deviate from your actual route, not just proximity." },
          { icon: "ti-car",                    title: "Car-specific",       desc: "Connector type, charging speed, and range are all matched to your specific EV model." },
          { icon: "ti-battery-2",              title: "Battery-aware",      desc: "SEVA checks if you can actually reach the station and calculates exactly how long to charge." },
          { icon: "ti-adjustments-horizontal", title: "Your preferences",   desc: "Set your price sensitivity, AC usage, and detour tolerance. SEVA ranks stations accordingly." },
          { icon: "ti-map-pin",                title: "Saved destinations", desc: "Save home, work, and frequent spots. SEVA gives bonus scores to nearby stations." },
          { icon: "ti-map",                    title: "Live map preview",   desc: "See your origin, charging stop, and destination on a real map before you go." },
        ].map(f => (
          <div key={f.title} style={{
            background: "var(--white)", border: "0.5px solid var(--border)",
            borderRadius: 12, padding: 20,
          }}>
            <div style={{
              width: 36, height: 36, borderRadius: 8,
              background: "var(--green-tag)", display: "flex",
              alignItems: "center", justifyContent: "center", marginBottom: 12,
            }}>
              <i className={`ti ${f.icon}`} style={{ fontSize: 18, color: "var(--primary)" }} />
            </div>
            <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text)", marginBottom: 6 }}>{f.title}</div>
            <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.6 }}>{f.desc}</div>
          </div>
        ))}
      </div>

      {/* ── Stats ── */}
      <div style={{
        display: "flex", gap: 0, justifyContent: "center",
        padding: "8px 32px 32px", maxWidth: 880, margin: "0 auto", width: "100%",
      }}>
        {[
          { num: "100+", label: "Stations in Egypt" },
          { num: "15+",  label: "EV models supported" },
          { num: "6",    label: "Scoring criteria" },
        ].map((s, i) => (
          <div key={s.label} style={{ display: "flex", alignItems: "center" }}>
            {i > 0 && <div style={{ width: 1, height: 40, background: "var(--border)", margin: "0 32px" }} />}
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 24, fontWeight: 600, color: "var(--primary)" }}>{s.num}</div>
              <div style={{ fontSize: 11, color: "var(--muted2)", marginTop: 2, textTransform: "uppercase", letterSpacing: "0.5px" }}>{s.label}</div>
            </div>
          </div>
        ))}
      </div>

      {/* ── Footer ── */}
      <div style={{
        padding: "16px 32px", borderTop: "0.5px solid var(--border)",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        marginTop: "auto",
      }}>
        <span style={{ fontSize: 11, color: "var(--muted2)" }}>
          German University in Cairo · Graduation Project 2026
        </span>
        <span style={{ fontSize: 11, color: "var(--muted)", display: "flex", alignItems: "center", gap: 4 }}>
          <i className="ti ti-shield-check" style={{ fontSize: 13 }} />
          Farah Mohamed Sadek
        </span>
      </div>

    </div>
  );
}