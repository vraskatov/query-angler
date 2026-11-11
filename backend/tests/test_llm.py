"""Tests für den LLM-Client. Ein MockTransport spielt den Server, es gibt kein Netzwerk."""

import asyncio
import json

import httpx
import pytest

from queryangler.llm import LLMError, LLMTimeoutError, LLMUnavailableError, OpenAICompatibleClient

MESSAGES = [{"role": "user", "content": "Was ist SQL?"}]

COMPLETION = {
    "model": "query-angler",
    "choices": [
        {"message": {"role": "assistant", "content": "  SQL ist eine Abfragesprache [1].  "}}
    ],
    "usage": {"prompt_tokens": 812, "completion_tokens": 42},
}


def client_with(handler, **kwargs):
    return OpenAICompatibleClient(
        "http://llm.test/v1", "query-angler", transport=httpx.MockTransport(handler), **kwargs
    )


def test_complete_sends_openai_format_and_parses_answer():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=COMPLETION)

    result = asyncio.run(client_with(handler, temperature=0.3).complete(MESSAGES))

    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["auth"] is None  # ohne Schlüssel kein Header
    assert seen["body"] == {
        "model": "query-angler",
        "messages": MESSAGES,
        "temperature": 0.3,
        "stream": False,
    }
    assert result.text == "SQL ist eine Abfragesprache [1]."
    assert (result.prompt_tokens, result.completion_tokens) == (812, 42)


def test_api_key_is_sent_as_bearer_token():
    def handler(request):
        assert request.headers["authorization"] == "Bearer geheim"
        return httpx.Response(200, json=COMPLETION)

    asyncio.run(client_with(handler, api_key="geheim").complete(MESSAGES))


def test_missing_usage_is_tolerated():
    data = {k: v for k, v in COMPLETION.items() if k != "usage"}
    result = asyncio.run(
        client_with(lambda request: httpx.Response(200, json=data)).complete(MESSAGES)
    )
    assert result.prompt_tokens is None


@pytest.mark.parametrize(
    ("handler", "error"),
    [
        (lambda request: httpx.Response(500, text="kaputt"), LLMError),
        (lambda request: httpx.Response(404, text="model not found"), LLMError),
    ],
)
def test_error_status_raises(handler, error):
    with pytest.raises(error, match="Fehler"):
        asyncio.run(client_with(handler).complete(MESSAGES))


def test_unreachable_server():
    def handler(request):
        raise httpx.ConnectError("Verbindung abgelehnt")

    with pytest.raises(LLMUnavailableError, match="Läuft Ollama"):
        asyncio.run(client_with(handler).complete(MESSAGES))


def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout("zu langsam")

    with pytest.raises(LLMTimeoutError):
        asyncio.run(client_with(handler).complete(MESSAGES))


def test_is_reachable():
    up = client_with(lambda request: httpx.Response(200, json={"data": []}))
    assert asyncio.run(up.is_reachable()) is True

    def down(request):
        raise httpx.ConnectError("aus")

    assert asyncio.run(client_with(down).is_reachable()) is False
