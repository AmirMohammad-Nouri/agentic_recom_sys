"""Ollama client for the supervisor model.

The model is used for exactly three bounded jobs (see docs/architecture.md):
  1. typed decisions   - choose one option from a fixed list (intent, next skill), with a JSON
                         schema enforced through Ollama's `format` parameter;
  2. slot extraction   - fill typed fields (criterion, recipe reference, time window ...);
  3. phrasing          - rewrite an answer from an evidence pack; the text is validated
                         and replaced by the template when it makes an unsupported claim.
It never ranks recipes, never writes database queries and never changes saved settings.
"""
import json
import time

import httpx


class LLMUnavailable(RuntimeError):
    pass


class OllamaLLM:
    def __init__(self, settings):
        self.s = settings
        self.model = settings.llm_model
        self._status = None
        self._status_ts = 0.0

    # ---- availability -------------------------------------------------------------
    def status(self, refresh=False):
        if not self.s.llm_enabled:
            return {"available": False, "reason": "disabled by FOODREC_LLM=0", "model": self.model}
        if not refresh and self._status and time.time() - self._status_ts < 20:
            return self._status
        try:
            r = httpx.get(f"{self.s.ollama_url}/api/tags", timeout=2.0)
            names = [m["name"] for m in r.json().get("models", [])]
            ok = any(n == self.model or n.split(":")[0] == self.model for n in names) or \
                any(n == self.model + ":latest" for n in names)
            emb = any(n.split(":")[0] == self.s.embed_model.split(":")[0] for n in names)
            st = {"available": ok, "model": self.model, "installed": names, "embed_model_ready": emb,
                  "reason": None if ok else f"model '{self.model}' is not pulled (ollama pull {self.model})"}
        except Exception as e:  # noqa: BLE001 - any connection failure means offline
            st = {"available": False, "model": self.model, "embed_model_ready": False,
                  "reason": f"Ollama not reachable at {self.s.ollama_url} ({type(e).__name__})"}
        self._status, self._status_ts = st, time.time()
        return st

    @property
    def available(self):
        return self.status()["available"]

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

    # ---- embeddings ----------------------------------------------------------------
    def embed(self, texts):
        body = {"model": self.s.embed_model, "input": texts, "truncate": True}
        if self.s.embed_on_cpu:
            body["options"] = {"num_gpu": 0}
        r = httpx.post(f"{self.s.ollama_url}/api/embed", json=body, timeout=120)
        r.raise_for_status()
        return r.json()["embeddings"]
