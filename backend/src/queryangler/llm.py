"""Client für Sprachmodelle über eine OpenAI-kompatible Schnittstelle.

„OpenAI-kompatibel“ beschreibt nur das Format der Anfrage (POST /chat/completions
mit model und messages). Ollama, Mistral, vLLM, LM Studio und andere bieten es an.
Welcher Anbieter antwortet, entscheidet allein die Adresse in der Konfiguration.
"""

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": "..."}


@dataclass
class LLMResult:
    text: str
    model: str
    seconds: float
    prompt_tokens: int | None = None  # wie der Anbieter sie zählt, falls er sie meldet
    completion_tokens: int | None = None


class LLMError(RuntimeError):
    """Das Sprachmodell hat mit einem Fehler geantwortet."""


class LLMUnavailableError(LLMError):
    """Der Dienst ist nicht erreichbar, z. B. weil Ollama nicht läuft."""


class LLMTimeoutError(LLMError):
    """Der Dienst hat nicht rechtzeitig geantwortet."""


class LLMClient(Protocol):
    model: str

    async def complete(self, messages: list[Message]) -> LLMResult: ...

    async def is_reachable(self) -> bool: ...


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        temperature: float = 0.2,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,  # für Tests ohne Netzwerk
    ) -> None:
        self.model = model
        self._temperature = temperature
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    async def complete(self, messages: list[Message]) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self._temperature,  # niedrig: sachlich statt kreativ
            "stream": False,
        }
        start = time.perf_counter()
        try:
            response = await self._client.post("chat/completions", json=payload)
        except httpx.TimeoutException as error:
            raise LLMTimeoutError("Das Sprachmodell hat nicht rechtzeitig geantwortet.") from error
        except httpx.TransportError as error:
            raise LLMUnavailableError(
                f"Das Sprachmodell ist nicht erreichbar ({self._client.base_url}). Läuft Ollama?"
            ) from error
        if response.status_code >= 400:
            raise LLMError(f"Fehler {response.status_code} vom Sprachmodell: {response.text[:300]}")

        data = response.json()
        usage = data.get("usage") or {}
        return LLMResult(
            text=data["choices"][0]["message"]["content"].strip(),
            model=data.get("model", self.model),
            seconds=time.perf_counter() - start,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    async def is_reachable(self) -> bool:
        try:
            response = await self._client.get("models", timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code < 400

    async def aclose(self) -> None:
        await self._client.aclose()
