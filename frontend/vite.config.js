// Entwicklung: `npm run dev` startet die Oberfläche auf Port 5173 und leitet die
// API-Aufrufe an das Backend auf Port 8000 weiter. So braucht es kein CORS.
// Auslieferung: `npm run build` schreibt nach dist/, FastAPI liefert das mit aus.
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const backend = "http://localhost:8000";
const api = ["/ask", "/health", "/info", "/sources", "/docs", "/openapi.json"];

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: Object.fromEntries(api.map((path) => [path, backend])),
  },
});
