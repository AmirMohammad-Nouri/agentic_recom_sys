"""Language-model clients for the supervisor, selected by llm.provider in the config:

  ollama      a local Ollama server (default)                            OllamaLLM (below)
  openai      any OpenAI-compatible chat API (OpenAI, Groq, Together,    llm_openai.OpenAICompatLLM
              OpenRouter, vLLM, LM Studio ...) at llm.base_url
  anthropic   the Claude API, through the official anthropic SDK         llm_anthropic.AnthropicLLM

Every client has the same small interface: status(), available, model, chat(...), embed(...).
API keys are read from the environment variable named in llm.api_key_env (never from the
config file). Embeddings come from llm.embed_provider (ollama | openai | none), independent
of the chat provider.

The model is used for exactly three bounded jobs (see docs/architecture.md):
  1. typed decisions   - choose one option from a fixed list (intent, next skill), with a JSON
                         schema enforced by the provider's structured-output feature;
  2. slot extraction   - fill typed fields (criterion, recipe reference, time window ...);
  3. phrasing          - rewrite an answer from an evidence pack; the text is validated
                         and replaced by the template when it makes an unsupported claim.
It never ranks recipes, never writes database queries and never changes saved settings.
"""
import json
import os
import re
import time

import httpx


class LLMUnavailable(RuntimeError):
    pass


def api_key(settings, default_env):
    """(variable name, key or None) for the configured provider."""
    name = (getattr(settings, "llm_api_key_env", "") or default_env).strip()
    return name, (os.environ.get(name) or None)


def parse_json(text):
    """JSON from a reply that may be wrapped in a code fence or have text around it."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            return json.loads(t[i:j + 1])
        raise


def schema_instruction(schema):
    return ("Reply with a single JSON object and nothing else. It must match this JSON schema:\n"
            + json.dumps(schema))


class BaseLLM:
    """Status caching and embeddings shared by all providers."""
    provider = "base"
    status_ttl = 20

    def __init__(self, settings):
        self.s = settings
        self.model = settings.llm_model
        self._status = None
        self._status_ts = 0.0

    def status(self, refresh=False):
        if not refresh and self._status and time.time() - self._status_ts < self.status_ttl \
                and self._status.get("enabled") == bool(self.s.llm_enabled):
            return self._status
        if not self.s.llm_enabled:
            st = {"available": False, "enabled": False, "provider": self.provider, "model": self.model,
                  "reason": "disabled in the config (llm.enabled)", "embed_model_ready": self._embed_ready()}
            self._status, self._status_ts = st, time.time()
            return st
        st = {"provider": self.provider, "enabled": True, **self._check()}
        st.setdefault("model", self.model)
        st.setdefault("embed_model_ready", self._embed_ready(st.get("installed") if self.provider == "ollama" else None))
        self._status, self._status_ts = st, time.time()
        return st

    def _check(self):
        raise NotImplementedError

    @property
    def available(self):
        return self.status()["available"]

    # ---- embeddings (llm.embed_provider) ----------------------------------------------
    def _embed_ready(self, ollama_models=None):
        prov = getattr(self.s, "embed_provider", "ollama")
        if prov == "none":
            return False
        if prov == "openai":
            name, key = api_key(self.s, "OPENAI_API_KEY")
            return bool(key) or "localhost" in (self.s.llm_base_url or "") or "127.0.0.1" in (self.s.llm_base_url or "")
        if ollama_models is None:
            try:
                r = httpx.get(f"{self.s.ollama_url}/api/tags", timeout=2.0)
                ollama_models = [m["name"] for m in r.json().get("models", [])]
            except Exception:  # noqa: BLE001 - offline means not ready
                return False
        return any(n.split(":")[0] == self.s.embed_model.split(":")[0] for n in ollama_models)

    def embed(self, texts):
        prov = getattr(self.s, "embed_provider", "ollama")
        if prov == "openai":
            base = (self.s.llm_base_url or "https://api.openai.com/v1").rstrip("/")
            _, key = api_key(self.s, "OPENAI_API_KEY")
            r = httpx.post(f"{base}/embeddings", json={"model": self.s.embed_model, "input": texts},
                           headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=120)
            r.raise_for_status()
            return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]
        if prov == "none":
            raise LLMUnavailable("embeddings are disabled (llm.embed_provider = none)")
        body = {"model": self.s.embed_model, "input": texts, "truncate": True}
        if self.s.embed_on_cpu:
            body["options"] = {"num_gpu": 0}
        r = httpx.post(f"{self.s.ollama_url}/api/embed", json=body, timeout=120)
        r.raise_for_status()
        return r.json()["embeddings"]


class OllamaLLM(BaseLLM):
    provider = "ollama"

    def _check(self):
        try:
            r = httpx.get(f"{self.s.ollama_url}/api/tags", timeout=2.0)
            names = [m["name"] for m in r.json().get("models", [])]
            ok = any(n == self.model or n.split(":")[0] == self.model for n in names) or \
                any(n == self.model + ":latest" for n in names)
            return {"available": ok, "installed": names,
                    "reason": None if ok else f"model '{self.model}' is not pulled (ollama pull {self.model})"}
        except Exception as e:  # noqa: BLE001 - any connection failure means offline
            return {"available": False, "installed": [], "embed_model_ready": False,
                    "reason": f"Ollama not reachable at {self.s.ollama_url} ({type(e).__name__})"}

    # ---- chat ------------------------------------------------------------------------
    def chat(self, messages, schema=None, think=None, on_thinking=None, temperature=0.0,
             max_tokens=600):
        """Return {"content", "data", "thinking", "tokens_in", "tokens_out", "ms"}.

        With `schema` the reply is parsed as JSON into "data" (ValueError if it does not parse).
        `on_thinking(text)` receives the model's reasoning tokens as they stream.
        """
        if not self.available:
            raise LLMUnavailable(self.status()["reason"])
        think = self.s.llm_think if think is None else think
        body = {"model": self.model, "messages": messages, "stream": True, "think": think,
                "options": {"temperature": temperature, "seed": self.s.llm_seed,
                            "num_ctx": self.s.llm_ctx, "num_predict": max_tokens}}
        if schema is not None:
            body["format"] = schema
        content, thinking, t_in, t_out = [], [], 0, 0
        t0 = time.perf_counter()
        try:
            with httpx.stream("POST", f"{self.s.ollama_url}/api/chat", json=body,
                              timeout=self.s.llm_timeout_s) as r:
                if r.status_code != 200:
                    raise LLMUnavailable(f"Ollama returned HTTP {r.status_code}: {r.read()[:200]!r}")
                for line in r.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    msg = chunk.get("message") or {}
                    if msg.get("thinking"):
                        thinking.append(msg["thinking"])
                        if on_thinking:
                            on_thinking(msg["thinking"])
                    if msg.get("content"):
                        content.append(msg["content"])
                    if chunk.get("done"):
                        t_in = chunk.get("prompt_eval_count", 0)
                        t_out = chunk.get("eval_count", 0)
        except httpx.HTTPError as e:
            self._status = None
            raise LLMUnavailable(f"{type(e).__name__}: {e}") from e
        text = "".join(content).strip()
        out = {"content": text, "data": None, "thinking": "".join(thinking).strip(),
               "tokens_in": t_in, "tokens_out": t_out, "ms": (time.perf_counter() - t0) * 1000}
        if schema is not None:
            out["data"] = json.loads(text)
        return out


def make_llm(settings):
    """The client for llm.provider."""
    prov = getattr(settings, "llm_provider", "ollama")
    if prov == "openai":
        from .llm_openai import OpenAICompatLLM
        return OpenAICompatLLM(settings)
    if prov == "anthropic":
        from .llm_anthropic import AnthropicLLM
        return AnthropicLLM(settings)
    return OllamaLLM(settings)
