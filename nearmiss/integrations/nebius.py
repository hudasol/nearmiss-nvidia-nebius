"""Nebius Token Factory client (OpenAI-compatible chat completions).

Server-side only: the API key is read from the environment and never logged, persisted
or sent to the browser. Timeouts, retries and error mapping are explicit.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
DEFAULT_MODEL = "nvidia/Nemotron-3_5-Lightning"
#: Fallbacks, in order, if the configured model is not in the account's live catalog.
#: Only NVIDIA open models are eligible.
NVIDIA_MODEL_PREFERENCE = (
    "nvidia/Nemotron-3_5-Lightning",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "nvidia/Nemotron-3-Ultra-550b-a55b",
    "nvidia/Llama-3_3-Nemotron-Super-49B-v1_5",
)
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class ModelError(Exception):
    """Base class for model integration failures. ``kind`` is safe to show to users."""

    kind = "model_error"


class ModelNotConfigured(ModelError):
    kind = "not_configured"


class ModelAuthError(ModelError):
    kind = "auth_error"


class ModelUnavailable(ModelError):
    kind = "unavailable"


class ModelTimeout(ModelError):
    kind = "timeout"


class ModelBadOutput(ModelError):
    kind = "invalid_output"


@dataclass(frozen=True)
class NebiusConfig:
    api_key: str | None
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_s: float = 60.0
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> NebiusConfig:
        return cls(
            api_key=os.environ.get("NEBIUS_API_KEY") or None,
            base_url=os.environ.get("NEBIUS_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            model=os.environ.get("NEBIUS_MODEL", DEFAULT_MODEL),
            timeout_s=float(os.environ.get("NEBIUS_TIMEOUT_S", "60")),
            max_retries=int(os.environ.get("NEBIUS_MAX_RETRIES", "2")),
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass
class ChatResult:
    content: str
    model_requested: str
    model_served: str
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float
    attempts: int
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


def strip_reasoning(text: str) -> str:
    """Remove <think> blocks some models emit before the answer."""
    text = _THINK.sub("", text)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


class TokenFactoryClient:
    def __init__(self, config: NebiusConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self._transport = transport
        self._resolved_model: str | None = None

    def _client(self) -> httpx.Client:
        if not self.config.api_key:
            raise ModelNotConfigured(
                "NEBIUS_API_KEY is not set. Model features are unavailable; deterministic "
                "simulation and evaluation still work."
            )
        return httpx.Client(
            base_url=self.config.base_url,
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            timeout=httpx.Timeout(self.config.timeout_s, connect=10.0),
            transport=self._transport,
        )

    def _request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], int]:
        attempts = 0
        last: Exception | None = None
        with self._client() as client:
            while attempts <= self.config.max_retries:
                attempts += 1
                try:
                    resp = client.request(method, path, json=json)
                except httpx.TimeoutException as exc:
                    last = ModelTimeout(
                        f"Token Factory did not respond within {self.config.timeout_s:.0f} s"
                    )
                    last.__cause__ = exc
                except httpx.HTTPError as exc:
                    last = ModelUnavailable(f"Could not reach Token Factory: {type(exc).__name__}")
                    last.__cause__ = exc
                else:
                    if resp.status_code in (401, 403):
                        raise ModelAuthError(
                            f"Token Factory rejected the API key (HTTP {resp.status_code})."
                        )
                    if resp.status_code == 404:
                        raise ModelUnavailable(
                            f"Model or endpoint not found (HTTP 404): {_detail(resp)}"
                        )
                    if resp.status_code in RETRYABLE_STATUS:
                        last = ModelUnavailable(
                            f"Token Factory returned HTTP {resp.status_code}: {_detail(resp)}"
                        )
                    elif resp.status_code >= 400:
                        raise ModelUnavailable(
                            f"Token Factory returned HTTP {resp.status_code}: {_detail(resp)}"
                        )
                    else:
                        try:
                            return resp.json(), attempts
                        except ValueError as exc:
                            raise ModelBadOutput(
                                "Token Factory returned a non-JSON response"
                            ) from exc
                if attempts <= self.config.max_retries:
                    time.sleep(min(8.0, 0.5 * 2 ** (attempts - 1)))
        assert last is not None
        raise last

    def list_models(self) -> list[str]:
        data, _ = self._request("GET", "/models")
        return sorted(str(m.get("id")) for m in data.get("data", []) if m.get("id"))

    def resolve_model(self) -> str:
        """The configured model if the live catalog lists it, else the first listed NVIDIA
        fallback. Raises if no eligible NVIDIA model is available to this key."""
        if self._resolved_model:
            return self._resolved_model
        available = set(self.list_models())
        for candidate in (self.config.model, *NVIDIA_MODEL_PREFERENCE):
            if candidate in available and candidate.lower().startswith("nvidia/"):
                self._resolved_model = candidate
                return candidate
        nvidia = sorted(
            m for m in available if m.lower().startswith("nvidia/") and "nemotron" in m.lower()
        )
        if nvidia:
            self._resolved_model = nvidia[0]
            return nvidia[0]
        raise ModelUnavailable("No NVIDIA Nemotron model is available to this Token Factory key.")

    def chat_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 1800,
        temperature: float = 0.2,
        model: str | None = None,
    ) -> ChatResult:
        model_id = model or self.resolve_model()
        body: dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            # Direct-output task: disable the model's reasoning trace (per model card).
            "chat_template_kwargs": {"enable_thinking": False},
        }
        t0 = time.perf_counter()
        data, attempts = self._request("POST", "/chat/completions", json=body)
        latency = (time.perf_counter() - t0) * 1000
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelBadOutput("Token Factory response had no message content") from exc
        usage = data.get("usage") or {}
        return ChatResult(
            content=strip_reasoning(content),
            model_requested=model_id,
            model_served=str(data.get("model") or model_id),
            finish_reason=choice.get("finish_reason"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=round(latency, 1),
            attempts=attempts,
            raw=data,
        )


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        detail = body.get("detail") or body.get("error") or body
    except ValueError:
        detail = resp.text
    return str(detail)[:300]
