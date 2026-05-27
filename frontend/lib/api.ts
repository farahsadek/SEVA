const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function sendMessage(message: string, sessionId: string, profile: object) {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId, profile }),
  });
  return res.json();
}

export async function login(name: string, pin: string) {
  const res = await fetch(`${BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, pin }),
  });
  return res.json();
}

export async function register(data: object) {
  const res = await fetch(`${BASE}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function clearMemory(sessionId: string) {
  const res = await fetch(`${BASE}/clear-memory`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  return res.json();
}

export async function getBrands(): Promise<string[]> {
  const res = await fetch(`${BASE}/brands`);
  const data = await res.json();
  return data.brands;
}

export async function getModels(brand: string): Promise<string[]> {
  const res = await fetch(`${BASE}/models/${encodeURIComponent(brand)}`);
  const data = await res.json();
  return data.models;
}

export async function getSpecs(model: string) {
  const res = await fetch(`${BASE}/specs/${encodeURIComponent(model)}`);
  const data = await res.json();
  return data.specs;
}

export async function updateProfile(data: object) {
  const res = await fetch(`${BASE}/update-profile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}