"""Pluggable summary engine: Pi, local Ollama, or an OpenAI-compatible API.

Detection order (S1): Pi installed -> Ollama serving the configured model ->
cloud key configured -> nothing. Without an engine the front keeps the
summary buttons disabled.

Every engine receives the same prompt (pi_bridge's section contract) and
returns the summary text; deltas stream through ``on_delta`` the same way
Pi's JSONL stream does. Ollama is 100% local; the cloud engine sends the
transcript to the configured provider (documented in the README).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import app_settings
import pi_bridge

OLLAMA_HOST = "http://127.0.0.1:11434"
_OLLAMA_PROBE_TIMEOUT = 1.5
_OLLAMA_CACHE_TTL = 30.0
_OLLAMA_CACHE: list = []  # [timestamp, model-or-None]


class SummaryError(RuntimeError):
    """A summary could not be produced (re-exported for callers)."""


def _ollama_model() -> str:
    return str(app_settings.get("ollama_model") or "llama3.1:8b")


def ollama_ready() -> str | None:
    """Return the model name when Ollama serves the configured model.

    Probed at most every _OLLAMA_CACHE_TTL: state publishes happen often
    and a dead Ollama must not add a 1.5 s stall to each one.
    """
    now = time.monotonic()
    if _OLLAMA_CACHE and now - _OLLAMA_CACHE[0] < _OLLAMA_CACHE_TTL:
        return _OLLAMA_CACHE[1]
    result = None
    try:
        with urllib.request.urlopen(
                f"{OLLAMA_HOST}/api/tags", timeout=_OLLAMA_PROBE_TIMEOUT) as r:
            tags = json.loads(r.read().decode("utf-8", errors="replace"))
        model = _ollama_model()
        installed = {str(m.get("name", "")).split(":")[0]
                     for m in tags.get("models", [])}
        result = model if (tags.get("models") and
                           model.split(":")[0] in installed) else None
    except (OSError, ValueError):
        result = None
    _OLLAMA_CACHE.clear()
    _OLLAMA_CACHE.extend([now, result])
    return result


def cloud_ready() -> bool:
    return bool(str(app_settings.get("cloud_base_url") or "")
                and str(app_settings.get("cloud_api_key") or ""))


def available_engines() -> dict:
    """What can run right now, for the front's chip and button states."""
    ollama = ollama_ready()
    return {
        "pi": pi_bridge.available(),
        "ollama": ollama or "",
        "cloud": cloud_ready(),
    }


def engine_label(engines: dict) -> str:
    """Chip text for the front: which engine the next summary will use."""
    if engines.get("pi"):
        return "Pi"
    if engines.get("ollama"):
        return f"Ollama ({engines['ollama']})"
    if engines.get("cloud"):
        return "API"
    return "sin motor"


def detect_backend() -> str:
    engines = available_engines()
    if engines["pi"]:
        return "pi"
    if engines["ollama"]:
        return "ollama"
    if engines["cloud"]:
        return "cloud"
    return "none"


def summarize(path: str, *, on_delta=None, cancel_event=None,
              timeout_s: float = 900.0) -> str:
    """Summarize one transcript file with the detected engine (S2)."""
    backend = detect_backend()
    if backend == "none":
        raise pi_bridge.SummaryError(
            "No hay motor de resumen: instalá Pi u Ollama, o configurá una "
            "API key en transcriptor_config.json.")
    if not os.path.exists(path):
        raise pi_bridge.SummaryError(f"No existe el archivo del transcript: {path}")
    try:
        content = Path(path).read_bytes().decode("utf-8", errors="replace")
    except OSError as exc:
        raise pi_bridge.SummaryError(
            f"No se pudo leer el transcript: {exc}") from exc
    prompt = pi_bridge._prompt_for(path, content)
    if backend == "pi":
        return pi_bridge.summarize(
            path, on_delta=on_delta, cancel_event=cancel_event,
            timeout_s=timeout_s)
    if backend == "ollama":
        return _summarize_ollama(prompt, on_delta, cancel_event, timeout_s)
    return _summarize_cloud(prompt, on_delta, cancel_event, timeout_s)


def _summarize_ollama(prompt, on_delta, cancel_event, timeout_s) -> str:
    payload = json.dumps({
        "model": _ollama_model(),
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat", data=payload,
        headers={"Content-Type": "application/json"})
    try:
        response = urllib.request.urlopen(request, timeout=timeout_s)
    except (OSError, urllib.error.URLError) as exc:
        raise pi_bridge.SummaryError(
            f"Ollama no respondió ({exc}). ¿Está corriendo y el modelo "
            f"'{_ollama_model()}' instalado? Probá: ollama pull {_ollama_model()}"
        ) from exc
    with response:
        return _consume_ollama_stream(
            iter(lambda: response.readline(), b""), on_delta, cancel_event)


def _consume_ollama_stream(lines, on_delta=None, cancel_event=None) -> str:
    """Parse Ollama's JSONL chat stream into the summary text."""
    chunks: list[str] = []
    for raw in lines:
        if cancel_event is not None and cancel_event.is_set():
            raise pi_bridge.SummaryError("Resumen cancelado por el usuario.")
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError):
            continue
        if record.get("error"):
            raise pi_bridge.SummaryError(f"Ollama: {record['error']}")
        delta = (record.get("message") or {}).get("content") or ""
        if delta:
            chunks.append(delta)
            if on_delta:
                on_delta(delta)
        if record.get("done"):
            break
    text = "".join(chunks).strip()
    if not text:
        raise pi_bridge.SummaryError("Ollama terminó sin producir texto.")
    return text


def _summarize_cloud(prompt, on_delta, cancel_event, timeout_s) -> str:
    base = str(app_settings.get("cloud_base_url")).rstrip("/")
    payload = json.dumps({
        "model": str(app_settings.get("cloud_model")),
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/chat/completions", data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {app_settings.get('cloud_api_key')}",
        })
    try:
        response = urllib.request.urlopen(request, timeout=timeout_s)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[-200:]
        except OSError:
            pass
        raise pi_bridge.SummaryError(
            f"La API respondió {exc.code}: {body or exc.reason}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise pi_bridge.SummaryError(
            f"No se pudo contactar la API: {exc}") from exc
    with response:
        return _consume_cloud_stream(
            iter(lambda: response.readline(), b""), on_delta, cancel_event)


def _consume_cloud_stream(lines, on_delta=None, cancel_event=None) -> str:
    """Parse an OpenAI-compatible SSE chat stream into the summary text."""
    chunks: list[str] = []
    for raw in lines:
        if cancel_event is not None and cancel_event.is_set():
            raise pi_bridge.SummaryError("Resumen cancelado por el usuario.")
        line = raw.strip()
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            record = json.loads(data)
        except ValueError:
            continue
        try:
            delta = record["choices"][0]["delta"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            delta = ""
        if delta:
            chunks.append(delta)
            if on_delta:
                on_delta(delta)
    text = "".join(chunks).strip()
    if not text:
        raise pi_bridge.SummaryError("La API terminó sin producir texto.")
    return text
