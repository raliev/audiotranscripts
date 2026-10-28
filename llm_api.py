"""LLM providers for Beautify and chat when Claude Code is not used ("api" engine).

Every provider is spoken to over the OpenAI-compatible chat-completions protocol
(the openai SDK with a base URL): OpenAI, Gemini, Anthropic and OpenRouter offer
such an endpoint, and so do local servers: Ollama, LM Studio, or any other ("custom":
vLLM, llama.cpp server, a corporate gateway).

API keys are kept out of settings.json, in .webui/secrets.json (mode 600);
the usual environment variables (GEMINI_API_KEY, OPENAI_API_KEY, ...) work too.
"""

import os
from urllib.parse import urlsplit

import sessions as S

PROVIDERS = {
    "gemini": {"name": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "model": "gemini-3.5-flash", "env": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
               "key_url": "https://aistudio.google.com/apikey"},
    "openai": {"name": "OpenAI (ChatGPT)", "base_url": "https://api.openai.com/v1",
               "model": "gpt-5", "env": ("OPENAI_API_KEY",),
               "key_url": "https://platform.openai.com/api-keys"},
    "anthropic": {"name": "Anthropic API (Claude)", "base_url": "https://api.anthropic.com/v1/",
                  "model": "claude-sonnet-5-5", "env": ("ANTHROPIC_API_KEY",),
                  "key_url": "https://console.anthropic.com/settings/keys"},
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "model": "google/gemini-2.5-flash", "env": ("OPENROUTER_API_KEY",),
                   "key_url": "https://openrouter.ai/keys"},
    "ollama": {"name": "Ollama (local)", "base_url": "http://localhost:11434/v1", "model": "", "env": (),
               "key_url": "https://ollama.com/library", "local": True},
    "lmstudio": {"name": "LM Studio (local)", "base_url": "http://localhost:1234/v1", "model": "", "env": (),
                 "key_url": "https://lmstudio.ai", "local": True},
    "custom": {"name": "Other (OpenAI-compatible)", "base_url": "", "model": "", "env": (),
               "key_url": "", "local": True},
}
SECRETS = S.WEBUI_DIR / "secrets.json"
MAX_OUTPUT = 32000   # Anthropic requires max_tokens; a full annotated transcript is long


# ── Keys ─────────────────────────────────────────────────────────────────────

def _secrets() -> dict:
    return S._read_json(SECRETS, {"api_keys": {}})


def stored_key(provider: str) -> str:
    return _secrets()["api_keys"].get(provider, "")


def env_key(provider: str) -> str:
    for name in PROVIDERS.get(provider, {}).get("env", ()):
        if os.environ.get(name):
            return os.environ[name]
    return ""


def api_key(provider: str) -> str:
    return stored_key(provider) or env_key(provider)


def set_key(provider: str, key: str | None):
    data = _secrets()
    if key:
        data["api_keys"][provider] = key.strip()
    else:
        data["api_keys"].pop(provider, None)
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    S._write_json(SECRETS, data, mode=0o600)


def mask(key: str) -> str:
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else ("•" * len(key))


# ── Configuration ────────────────────────────────────────────────────────────

NOT_OPENAI = ("The server answered, but not in the OpenAI format: check the host (base URL). "
              "OpenAI-compatible servers usually end with /v1, e.g. http://localhost:1234/v1")


def normalize_base_url(provider: str, url: str) -> str:
    """Local servers (Ollama, LM Studio, vLLM, llama.cpp) serve the OpenAI API under /v1:
    "192.168.0.5:1234" or "http://host:1234" means http://host:1234/v1. A given path is kept."""
    url = (url or "").strip()
    if not url or not PROVIDERS.get(provider, PROVIDERS["custom"]).get("local"):
        return url
    if "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    if parts.netloc and parts.path.strip("/") == "":
        url = url.rstrip("/") + "/v1"
    return url


def config(settings: dict | None = None) -> dict:
    """The effective provider settings: provider, base_url, model, key."""
    s = settings or S.load_settings()
    provider = s.get("api_provider") or "gemini"
    p = PROVIDERS.get(provider, PROVIDERS["custom"])
    base_url = normalize_base_url(provider, s.get("api_base_url") or "") or p["base_url"]
    key = api_key(provider)
    if key and not p.get("local") and urlsplit(base_url).hostname != urlsplit(p["base_url"]).hostname:
        key = ""    # a provider's key goes to that provider only; other hosts: the "custom" provider
    return {"provider": provider, "base_url": base_url,
            "model": (s.get("api_model") or "").strip() or p["model"], "key": key}


def local_provider(provider: str) -> bool:
    """Local servers and custom hosts: an API key is optional."""
    return bool(PROVIDERS.get(provider, PROVIDERS["custom"]).get("local"))


def problem(cfg: dict) -> str:
    """Why this configuration cannot work yet ('' when it can)."""
    if not cfg["base_url"]:
        return "Set the API host (base URL)"
    if not cfg["model"]:
        return "Choose a model"
    if not cfg["key"] and not local_provider(cfg["provider"]):   # local servers often need no key
        return f"Add the {PROVIDERS[cfg['provider']]['name']} API key"
    return ""


def status(settings: dict, claude_found: bool) -> dict:
    """What the UI needs to know: is Beautify/chat usable, and with what."""
    engine = settings.get("beautify_engine") or "claude"
    if engine == "api":
        cfg = config(settings)
        why = problem(cfg)
        label = f"{PROVIDERS.get(cfg['provider'], PROVIDERS['custom'])['name']} · {cfg['model'] or '?'}"
    else:
        why = "" if claude_found else "Claude Code (the claude CLI) is not installed"
        label = "Claude Code"
    return {"engine": engine, "ready": not why, "problem": why, "label": label,
            "setup_done": bool(settings.get("ai_setup_done"))}


def public(settings: dict) -> dict:
    """Provider list and saved values for the setup dialog (keys masked)."""
    out = {}
    for pid, p in PROVIDERS.items():
        k = stored_key(pid)
        out[pid] = {"name": p["name"], "base_url": p["base_url"], "model": p["model"], "key_url": p["key_url"],
                    "key": mask(k) if k else "", "env_key": bool(env_key(pid)), "local": bool(p.get("local"))}
    return {"providers": out, "provider": settings.get("api_provider") or "gemini",
            "model": settings.get("api_model") or "", "base_url": settings.get("api_base_url") or ""}


# ── Calls ────────────────────────────────────────────────────────────────────

def client(cfg: dict, timeout: float = 900):
    from openai import OpenAI
    return OpenAI(api_key=cfg["key"] or "none", base_url=cfg["base_url"], timeout=timeout, max_retries=1)


def _kwargs(cfg: dict) -> dict:
    return {"max_tokens": MAX_OUTPUT} if cfg["provider"] == "anthropic" else {}


class Cancelled(Exception):
    """stream() was stopped by its should_stop()."""


def stream(cfg: dict, messages: list[dict], on_delta=None, on_reasoning=None, info: dict | None = None,
           should_stop=None) -> str:
    """Run a chat completion, streaming; returns the whole reply. on_reasoning(text) gets the
    model's thinking, where the provider streams it (DeepSeek, OpenRouter, …). info, if given,
    gets "finish_reason" ("length" = the reply was cut off at the output limit).
    should_stop() is checked on every piece: when true, raises Cancelled. Whatever ends the
    reply early (that, or an exception from a callback) closes the connection, so the server
    stops generating too (a local model would otherwise keep the GPU busy to the end)."""
    why = problem(cfg)
    if why:
        raise RuntimeError(why)
    parts = []
    resp = client(cfg).chat.completions.create(model=cfg["model"], messages=messages, stream=True, **_kwargs(cfg))
    answered = False
    try:
        for chunk in resp:
            if should_stop and should_stop():
                raise Cancelled("Cancelled")
            if not getattr(chunk, "choices", None):
                continue
            answered = True
            if info is not None and getattr(chunk.choices[0], "finish_reason", None):
                info["finish_reason"] = chunk.choices[0].finish_reason
            delta = chunk.choices[0].delta
            if on_reasoning:
                r = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if isinstance(r, str) and r:
                    on_reasoning(r)
            d = delta.content or ""
            if d:
                parts.append(d)
                if on_delta:
                    on_delta(d)
    finally:
        resp.close()
    text = "".join(parts)
    if not answered:
        raise RuntimeError(NOT_OPENAI)
    if not text.strip():
        raise RuntimeError("The model returned an empty answer")
    return text


def test(cfg: dict) -> dict:
    """Check the key/host: list the models, then (if a model is set) ask for one word."""
    if not cfg["base_url"]:
        return {"ok": False, "error": "Set the API host (base URL)"}
    c = client(cfg, timeout=40)
    models = []
    try:
        models = sorted(m.id.removeprefix("models/") for m in c.models.list() if getattr(m, "id", None))
    except Exception as e:
        listing_error = explain(e)
    else:
        listing_error = ""
    if cfg["model"]:
        try:
            r = c.chat.completions.create(model=cfg["model"], messages=[{"role": "user", "content": "Reply with: OK"}],
                                          **({"max_tokens": 10} if cfg["provider"] == "anthropic" else {}))
            if not getattr(r, "choices", None):
                return {"ok": False, "models": models, "error": NOT_OPENAI, "base_url": cfg["base_url"]}
            reply = (r.choices[0].message.content or "").strip()
            return {"ok": True, "models": models, "reply": reply[:40], "base_url": cfg["base_url"]}
        except Exception as e:
            return {"ok": False, "models": models, "error": explain(e)}
    if listing_error:
        return {"ok": False, "error": listing_error}
    return {"ok": True, "models": models}


def explain(e: Exception) -> str:
    name = type(e).__name__
    msg = str(e)
    low = msg.lower()
    if name in ("AuthenticationError", "PermissionDeniedError") or " 401" in msg or (
            "api key" in low and any(w in low for w in ("valid", "invalid", "incorrect", "expired"))) or "x-api-key" in low:
        return "The API key was rejected"
    if name == "NotFoundError" or " 404" in msg:
        return f"Not found (wrong model name or host): {msg[:200]}"
    if name in ("APIConnectionError", "APITimeoutError"):
        return "Could not reach the API host (internet connection, or the base URL; is the local server running?)"
    if "context length" in low or "context window" in low or "maximum context" in low:
        return ("The request doesn't fit the model's context (as loaded on the server: LM Studio loads a model on "
                "demand with its default context length; load it yourself with a larger Context Length)")
    if name == "RateLimitError" or " 429" in msg:
        return f"Rate limit or quota exceeded: {msg[:200]}"
    return f"{name}: {msg[:300]}"

