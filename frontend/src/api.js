// Alle Aufrufe an das Backend an einer Stelle. Fehlermeldungen des Backends
// (Feld "detail") werden so weitergegeben, dass die Oberfläche sie anzeigen kann.

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("Der Server ist nicht erreichbar. Läuft das Backend?");
  }
  if (response.status === 204) return null;
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(describe(body?.detail) || `Fehler ${response.status}`);
  }
  return body;
}

// FastAPI liefert bei Prüffehlern (422) eine Liste, sonst einen Text.
function describe(detail) {
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join("; ");
  return detail;
}

// history: frühere Wortwechsel als [{ question, answer }], älteste zuerst.
// Der Server speichert nichts; das Gedächtnis des Tutors liegt im Browser.
export function ask(question, mode, history = []) {
  return request("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, mode, history }),
  });
}

export const getInfo = () => request("/info");
export const getSources = () => request("/sources");

export function uploadSource(file, title, license) {
  const form = new FormData();
  form.append("file", file);
  form.append("title", title);
  form.append("license", license);
  return request("/sources", { method: "POST", body: form });
}

export function removeSource(id) {
  return request(`/sources/${encodeURIComponent(id)}`, { method: "DELETE" });
}
