"""HTTP-Schnittstelle des Tutors.

Start (im Ordner backend):  uv run uvicorn queryangler.api.app:app --reload
Interaktive Dokumentation:  http://localhost:8000/docs
Oberfläche:                 http://localhost:8000/  (wenn frontend/dist gebaut ist)

Modell und Index werden einmal beim Start geladen, nicht pro Anfrage. Fehlt etwas
(kein Index, Modell nicht heruntergeladen), startet der Server trotzdem: /health
nennt dann den Grund, /ask antwortet mit 503.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from queryangler import config
from queryangler.components import load_retriever
from queryangler.index.embedder import ModelUnavailableError
from queryangler.index.store import IndexUnavailableError
from queryangler.llm import LLMError, LLMTimeoutError, LLMUnavailableError, OpenAICompatibleClient
from queryangler.prompt import Mode
from queryangler.sources import MAX_BYTES, Source, SourceError, SourceManager
from queryangler.tutor import Answer, Tutor


def build_default_tutor() -> Tutor:
    """Baut den Tutor aus der Konfiguration (Umgebungsvariablen QUERYANGLER_*)."""
    retriever = load_retriever()
    llm = OpenAICompatibleClient(
        config.LLM_BASE_URL,
        config.LLM_MODEL,
        api_key=config.LLM_API_KEY,
        temperature=config.LLM_TEMPERATURE,
        timeout=config.LLM_TIMEOUT,
    )
    return Tutor(retriever, llm, context_tokens=config.LLM_CONTEXT_TOKENS)


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


class Info(BaseModel):
    book_title: str
    book_author: str
    book_license: str
    book_chapters: int
    book_chunks: int
    uploads: int
    upload_chunks: int
    embedding_model: str
    llm_model: str
    llm_reachable: bool


def create_app(
    build_tutor: Callable[[], Tutor] = build_default_tutor,
    upload_dir: Path = config.UPLOAD_DIR,
    frontend_dir: Path = config.FRONTEND_DIST,
) -> FastAPI:
    """build_tutor ist austauschbar: Die Tests übergeben einen Tutor mit Ersatzteilen."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.tutor = None
        app.state.startup_error = None
        app.state.sources = None
        try:
            app.state.tutor = build_tutor()
            retriever = app.state.tutor.retriever
            app.state.sources = SourceManager(retriever.collection, retriever.embedder, upload_dir)
            # Modell im Hintergrund vorladen; der Server ist sofort erreichbar
            warm_up = getattr(app.state.tutor.llm, "warm_up", None)
            if warm_up:
                app.state.warm_up = asyncio.create_task(warm_up())
        except (IndexUnavailableError, ModelUnavailableError) as error:
            app.state.startup_error = str(error)
        yield
        task = getattr(app.state, "warm_up", None)
        if task and not task.done():
            task.cancel()
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

    # --- Wissensbasis: Übersicht und eigenes Material der Lehrenden ----------------

    def get_sources(request: Request) -> SourceManager:
        if request.app.state.sources is None:
            raise HTTPException(503, request.app.state.startup_error or "Tutor nicht bereit.")
        return request.app.state.sources

    @app.get("/info", response_model=Info)
    async def info(
        tutor: Annotated[Tutor, Depends(get_tutor)],
        sources: Annotated[SourceManager, Depends(get_sources)],
    ) -> Info:
        metas = tutor.retriever.collection.get(include=["metadatas"])["metadatas"]
        book = [m for m in metas if m.get("source") != "upload"]
        return Info(
            book_title=config.BOOK_TITLE,
            book_author=config.BOOK_AUTHOR,
            book_license=config.BOOK_LICENSE,
            book_chapters=len({m["chapter"] for m in book}),
            book_chunks=len(book),
            uploads=len(sources.entries()),
            upload_chunks=len(metas) - len(book),
            embedding_model=tutor.retriever.embedder.name,
            llm_model=tutor.llm.model,
            llm_reachable=await tutor.llm.is_reachable(),
        )

    @app.get("/sources", response_model=list[Source])
    async def list_sources(sources: Annotated[SourceManager, Depends(get_sources)]) -> list[Source]:
        return sources.entries()

    @app.post("/sources", response_model=Source, status_code=201)
    async def add_source(
        tutor: Annotated[Tutor, Depends(get_tutor)],
        sources: Annotated[SourceManager, Depends(get_sources)],
        file: Annotated[UploadFile, File(description="Markdown (.md) oder Text (.txt), UTF-8")],
        title: Annotated[str, Form(min_length=1, max_length=200)],
        license: Annotated[str, Form(min_length=1, max_length=200)],
    ) -> Source:
        data = await file.read(MAX_BYTES + 1)  # ein Byte mehr: so fällt "zu groß" auf
        try:
            # Einbetten rechnet auf der CPU, deshalb in einem eigenen Thread
            source = await asyncio.to_thread(sources.add, file.filename or "", data, title, license)
        except SourceError as error:
            raise HTTPException(400, str(error)) from error
        tutor.retriever.refresh()
        return source

    @app.delete("/sources/{source_id}", status_code=204)
    async def remove_source(
        source_id: str,
        tutor: Annotated[Tutor, Depends(get_tutor)],
        sources: Annotated[SourceManager, Depends(get_sources)],
    ) -> Response:
        try:
            sources.remove(source_id)
        except KeyError as error:
            raise HTTPException(404, "Diese Quelle gibt es nicht.") from error
        tutor.retriever.refresh()
        return Response(status_code=204)

    # Die gebaute Oberfläche ausliefern. Als Letztes eingehängt, damit /ask, /docs usw.
    # Vorrang haben. Ohne gebaute Oberfläche läuft die API trotzdem.
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app


app = create_app()
