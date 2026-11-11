"""HTTP-Schnittstelle des Tutors.

Start (im Ordner backend):  uv run uvicorn queryangler.api.app:app --reload
Interaktive Dokumentation:  http://localhost:8000/docs

Modell und Index werden einmal beim Start geladen, nicht pro Anfrage. Fehlt etwas
(kein Index, Modell nicht heruntergeladen), startet der Server trotzdem: /health
nennt dann den Grund, /ask antwortet mit 503.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from queryangler import config
from queryangler.index.embedder import EmbedderUnavailableError, SentenceTransformerEmbedder
from queryangler.index.store import IndexUnavailableError, open_index
from queryangler.llm import LLMError, LLMTimeoutError, LLMUnavailableError, OpenAICompatibleClient
from queryangler.prompt import Mode
from queryangler.retrieval import Retriever
from queryangler.tutor import Answer, Tutor


def build_default_tutor() -> Tutor:
    """Baut den Tutor aus der Konfiguration (Umgebungsvariablen QUERYANGLER_*)."""
    # Zuerst den Index öffnen: Das geht schnell und scheitert früh, falls er fehlt.
    collection = open_index(config.INDEX_DIR, config.EMBED_MODEL, config.EMBED_REVISION)
    embedder = SentenceTransformerEmbedder(
        config.EMBED_MODEL,
        revision=config.EMBED_REVISION,
        device=config.EMBED_DEVICE,
        offline=True,  # der Server lädt nie etwas aus dem Netz nach
    )
    llm = OpenAICompatibleClient(
        config.LLM_BASE_URL,
        config.LLM_MODEL,
        api_key=config.LLM_API_KEY,
        temperature=config.LLM_TEMPERATURE,
        timeout=config.LLM_TIMEOUT,
    )
    return Tutor(Retriever(collection, embedder), llm, context_tokens=config.LLM_CONTEXT_TOKENS)


class AskRequest(BaseModel):
    question: str = Field(
        min_length=3,
        max_length=1000,
        examples=["Was ist der Unterschied zwischen WHERE und HAVING?"],
    )
    mode: Mode = "erklaeren"
    k: int = Field(default=5, ge=1, le=10, description="Anzahl der Auszüge aus dem Buch")


class Health(BaseModel):
    status: str  # "ok" oder "eingeschränkt"
    tutor_ready: bool
    llm_reachable: bool
    llm_model: str | None
    detail: str | None = None


def create_app(build_tutor: Callable[[], Tutor] = build_default_tutor) -> FastAPI:
    """build_tutor ist austauschbar: Die Tests übergeben einen Tutor mit Ersatzteilen."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.tutor = None
        app.state.startup_error = None
        try:
            app.state.tutor = build_tutor()
        except (IndexUnavailableError, EmbedderUnavailableError) as error:
            app.state.startup_error = str(error)
        yield
        aclose = getattr(getattr(app.state.tutor, "llm", None), "aclose", None)
        if aclose:
            await aclose()

    app = FastAPI(
        title="Query Angler",
        description="SQL-Tutor auf Grundlage des Wikibooks „Einführung in SQL“ (CC BY-SA 4.0).",
        version="0.1.0",
        lifespan=lifespan,
    )

    def get_tutor(request: Request) -> Tutor:
        tutor = request.app.state.tutor
        if tutor is None:
            raise HTTPException(503, request.app.state.startup_error or "Tutor nicht bereit.")
        return tutor

    @app.post("/ask", response_model=Answer)
    async def ask(body: AskRequest, tutor: Annotated[Tutor, Depends(get_tutor)]) -> Answer:
        try:
            return await tutor.ask(body.question, mode=body.mode, k=body.k)
        except ValueError as error:  # z. B. eine Frage nur aus Leerzeichen
            raise HTTPException(422, str(error)) from error
        except LLMUnavailableError as error:
            raise HTTPException(503, str(error)) from error
        except LLMTimeoutError as error:
            raise HTTPException(504, str(error)) from error
        except LLMError as error:
            raise HTTPException(502, str(error)) from error

    @app.get("/health", response_model=Health)
    async def health(request: Request) -> Health:
        tutor: Tutor | None = request.app.state.tutor
        reachable = await tutor.llm.is_reachable() if tutor else False
        return Health(
            status="ok" if tutor and reachable else "eingeschränkt",
            tutor_ready=tutor is not None,
            llm_reachable=reachable,
            llm_model=tutor.llm.model if tutor else None,
            detail=request.app.state.startup_error,
        )

    return app


app = create_app()
