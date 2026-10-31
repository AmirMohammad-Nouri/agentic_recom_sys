"""The Claude API as the supervisor model (llm.provider = "anthropic"), via the official SDK.

    pip install -e ".[anthropic]"
    llm.model        e.g. claude-opus-5-5 (default), claude-sonnet-5-5, claude-haiku-5-5
    llm.effort       low by default: the supervisor only classifies and rewrites short answers
    llm.api_key_env  default ANTHROPIC_API_KEY (the SDK also finds an `ant auth login` profile)
    llm.fallbacks    server-side refusal fallback on the models that support it (on by default)

Structured output uses output_config.format (json_schema). If the API rejects a schema, the
call is retried once with the schema as an instruction and the reply parsed as JSON. A
refusal (stop_reason "refusal") raises LLMUnavailable, so the app falls back to its rules
and templates for that turn.
"""
import time

from .llm import BaseLLM, LLMUnavailable, api_key, parse_json, schema_instruction

DEFAULT_MODEL = "claude-opus-5-5"
# models that accept the server-side refusal fallback ("default" routing)
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLM(BaseLLM):
    provider = "anthropic"
    status_ttl = 120

    def __init__(self, settings):
        super().__init__(settings)
        if not self.model or self.model.startswith(("qwen", "llama", "gpt", "mistral")):
            self.model = DEFAULT_MODEL
        self._client = None

    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise LLMUnavailable("the anthropic package is not installed (pip install -e \".[anthropic]\")") from e
            _, key = api_key(self.s, "ANTHROPIC_API_KEY")
            kw = {"timeout": float(self.s.llm_timeout_s), "max_retries": 2}
            if key:
                kw["api_key"] = key
            if self.s.llm_base_url:
                kw["base_url"] = self.s.llm_base_url
            self._client = anthropic.Anthropic(**kw)
        return self._client

    def _check(self):
        try:
            import anthropic
        except ImportError:
            return {"available": False, "installed": [],
                    "reason": "the anthropic package is not installed (pip install -e \".[anthropic]\")"}
        name, _ = api_key(self.s, "ANTHROPIC_API_KEY")
        try:
            self.client().models.retrieve(self.model)
            return {"available": True, "installed": [self.model], "reason": None}
        except anthropic.AuthenticationError:
            self._client = None
            return {"available": False, "installed": [],
                    "reason": f"no valid Claude API credentials (set {name}, or run `ant auth login`)"}
        except anthropic.NotFoundError:
            return {"available": False, "installed": [], "reason": f"unknown model '{self.model}'"}
        except anthropic.APIConnectionError as e:
            return {"available": False, "installed": [], "reason": f"Claude API not reachable ({type(e).__name__})"}
        except anthropic.APIStatusError as e:
            return {"available": False, "installed": [], "reason": f"Claude API error {e.status_code}"}
        except TypeError as e:  # the SDK found no api key, token or profile
            if "authentication" in str(e).lower():
                self._client = None
                return {"available": False, "installed": [],
                        "reason": f"no Claude API credentials (set {name}, or run `ant auth login`)"}
            return {"available": False, "installed": [], "reason": f"{type(e).__name__}: {e}"}

    def chat(self, messages, schema=None, think=None, on_thinking=None, temperature=0.0, max_tokens=600):
        import anthropic
        if not self.available:
            raise LLMUnavailable(self.status()["reason"])
        try:
            return self._chat(messages, schema, think, on_thinking, max_tokens, structured=True)
        except anthropic.BadRequestError as e:
            if schema is None:
                raise LLMUnavailable(f"Claude API rejected the request: {e.message}") from e
            # e.g. a schema feature the structured-output validator does not accept
            return self._chat(messages, schema, think, on_thinking, max_tokens, structured=False)
        except anthropic.APIConnectionError as e:
            self._status = None
            raise LLMUnavailable(f"Claude API not reachable ({type(e).__name__})") from e
        except anthropic.APIStatusError as e:
            raise LLMUnavailable(f"Claude API error {e.status_code}: {e.message}") from e

    def _chat(self, messages, schema, think, on_thinking, max_tokens, structured):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
        output_config = {"effort": self.s.llm_effort}
        if schema is not None:
            if structured:
                output_config["format"] = {"type": "json_schema", "schema": schema}
            else:
                system = (system + "\n\n" if system else "") + schema_instruction(schema)
        kw = {"model": self.model, "max_tokens": max(int(max_tokens), int(self.s.llm_max_tokens)),
              "messages": msgs, "output_config": output_config}
        if system:
            kw["system"] = system
        think = self.s.llm_think if think is None else think
        if think:
            kw["thinking"] = {"type": "adaptive", "display": "summarized"}
        if self.s.llm_fallbacks and self.model in FALLBACK_MODELS and not self.s.llm_base_url:
            kw["betas"] = [FALLBACK_BETA]
            kw["fallbacks"] = "default"
        t0 = time.perf_counter()
        with self.client().beta.messages.stream(**kw) as stream:
            for event in stream:
                if on_thinking and event.type == "content_block_delta" and event.delta.type == "thinking_delta":
                    on_thinking(event.delta.thinking)
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise LLMUnavailable("the model declined this request")
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        thinking = "".join(getattr(b, "thinking", "") or "" for b in msg.content if b.type == "thinking").strip()
        out = {"content": text, "data": None, "thinking": thinking,
               "tokens_in": msg.usage.input_tokens, "tokens_out": msg.usage.output_tokens,
               "ms": (time.perf_counter() - t0) * 1000}
        if schema is not None:
            out["data"] = parse_json(text)
        return out
