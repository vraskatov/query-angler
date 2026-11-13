# Query Angler: ein Image mit API und Oberfläche.
# Das Sprachmodell läuft in einem eigenen Container (siehe compose.yaml).

# ---------- Stufe 1: Oberfläche bauen ----------
FROM node:22-slim AS frontend
WORKDIR /app/frontend
# Erst nur die Paketlisten: Die Installation bleibt im Cache, solange sie sich nicht ändern
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- Stufe 2: Python-App ----------
FROM python:3.12-slim

# uv als fertige Datei aus dem offiziellen Image holen; Version wie lokal (uv --version)
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models/huggingface \
    PATH="/app/backend/.venv/bin:$PATH"

# Eigener Benutzer statt root. UID 1000 entspricht meist dem ersten Benutzer auf Linux,
# dann gehören Dateien im eingebundenen Ordner data/ beiden.
RUN useradd --create-home --uid 1000 angler \
 && mkdir -p /models/huggingface /app/backend /app/data \
 && chown -R angler:angler /models /app
USER angler
WORKDIR /app/backend

# Abhängigkeiten zuerst, ohne unseren Code: teuer, ändert sich selten
COPY --chown=angler:angler backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Dann der Code. Die Ordner bleiben wie im Repo, denn config.py findet die Wurzel
# über den eigenen Pfad, und die Oberfläche wird unter /app/frontend/dist erwartet.
COPY --chown=angler:angler backend/src ./src
RUN uv sync --frozen --no-dev
COPY --from=frontend --chown=angler:angler /app/frontend/dist /app/frontend/dist

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"
CMD ["uvicorn", "queryangler.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
