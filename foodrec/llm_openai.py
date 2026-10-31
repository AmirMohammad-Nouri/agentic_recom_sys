"""Any OpenAI-compatible chat-completions API (llm.provider = "openai").

Works with OpenAI, Groq, Together, OpenRouter, Mistral's OpenAI endpoint, vLLM, LM Studio,
llama.cpp server and Ollama's own /v1 endpoint: set llm.base_url and llm.model, and put the
key in the environment variable named by llm.api_key_env (default OPENAI_API_KEY; a local
server usually needs none).

Structured output: tries response_format json_schema, then json_object, then a plain
instruction, because servers differ in what they accept; the first that works is remembered.
"""
import json
import time

import httpx

from .llm import BaseLLM, LLMUnavailable, api_key, parse_json, schema_instruction

DEFAULT_BASE = "https://api.openai.com/v1"
JSON_MODES = ("json_schema", "json_object", "prompt")


class OpenAICompatLLM(BaseLLM):
    provider = "openai"

    def __init__(self, settings):
        super().__init__(settings)
        self._json_mode = None

    @property
    def base(self):
        return (self.s.llm_base_url or DEFAULT_BASE).rstrip("/")

    def _headers(self):
        _, key = api_key(self.s, "OPENAI_API_KEY")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _check(self):
        name, key = api_key(self.s, "OPENAI_API_KEY")
        local = any(h in self.base for h in ("localhost", "127.0.0.1", "0.0.0.0"))
        if not key and not local:
            return {"available": False, "installed": [], "reason": f"no API key: set the environment variable {name}"}
        try:
            r = httpx.get(f"{self.base}/models", headers=self._headers(), timeout=4.0)
        except httpx.HTTPError as e:
            return {"available": False, "installed": [], "reason": f"{self.base} not reachable ({type(e).__name__})"}
        if r.status_code in (401, 403):
            return {"available": False, "installed": [], "reason": f"the API rejected the key in {name} (HTTP {r.status_code})"}
        names = []
        if r.status_code == 200:
            try:
                names = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
            except ValueError:
                names = []
        if names and self.model not in names:
            return {"available": False, "installed": names,
                    "reason": f"model '{self.model}' is not offered by {self.base}"}
        return {"available": True, "installed": names, "reason": None}

    def chat(self, messages, schema=None, think=None, on_thinking=None, temperature=0.0, max_tokens=600):
        if not self.available:
            raise LLMUnavailable(self.status()["reason"])
        modes = [self._json_mode] if self._json_mode else list(JSON_MODES)
        last = None
        for mode in (modes if schema is not None else [None]):
            try:
                out = self._chat(messages, schema, mode, on_thinking, temperature, max_tokens)
                if schema is not None:
                    self._json_mode = mode
                return out
            except _Rejected as e:  # this server does not accept that response_format
                last = e
        raise LLMUnavailable(f"structured output not accepted: {last}")

    def _chat(self, messages, schema, mode, on_thinking, temperature, max_tokens):
        msgs = [dict(m) for m in messages]
        body = {"model": self.model, "messages": msgs, "stream": True, "temperature": temperature,
                "max_tokens": max(max_tokens, min(self.s.llm_max_tokens, 4096)), "seed": self.s.llm_seed}
        if schema is not None:
            if mode == "json_schema":
                body["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": "output", "schema": schema}}
            else:
                if mode == "json_object":
                    body["response_format"] = {"type": "json_object"}
                msgs.insert(0, {"role": "system", "content": schema_instruction(schema)})
        content, thinking, usage = [], [], {}
        t0 = time.perf_counter()
        try:
            with httpx.stream("POST", f"{self.base}/chat/completions", json=body, headers=self._headers(),
                              timeout=self.s.llm_timeout_s) as r:
                if r.status_code == 400 and schema is not None and mode != "prompt":
                    raise _Rejected(r.read()[:200].decode(errors="replace"))
                if r.status_code != 200:
                    raise LLMUnavailable(f"HTTP {r.status_code}: {r.read()[:300].decode(errors='replace')}")
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    usage = chunk.get("usage") or usage
                    for ch in chunk.get("choices") or []:
                        d = ch.get("delta") or {}
                        reasoning = d.get("reasoning_content") or d.get("reasoning")
                        if reasoning:
                            thinking.append(reasoning)
                            if on_thinking:
                                on_thinking(reasoning)
                        if d.get("content"):
                            content.append(d["content"])
        except httpx.HTTPError as e:
            self._status = None
            raise LLMUnavailable(f"{type(e).__name__}: {e}") from e
        text = "".join(content).strip()
        out = {"content": text, "data": None, "thinking": "".join(thinking).strip(),
               "tokens_in": usage.get("prompt_tokens", 0), "tokens_out": usage.get("completion_tokens", 0),
               "ms": (time.perf_counter() - t0) * 1000}
        if schema is not None:
            out["data"] = parse_json(text)
        return out


class _Rejected(Exception):
    pass
