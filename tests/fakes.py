"""Test doubles for the supervisor language model (no Ollama needed)."""
import json


class FakeLLM:
    """Scripted stand-in for OllamaLLM. `intent` is returned for schema calls that ask for an
    intent, `next` for follow-up decisions, and `answer` for free-text phrasing calls."""
    model = "fake-model"

    def __init__(self, intent=None, answer="Here you go.", next_step="answer", thinking="Let me check the list."):
        self.intent, self.answer, self.next_step, self.thinking = intent or {"intent": "help"}, answer, next_step, thinking
        self.calls = []

    def status(self, refresh=False):
        return {"available": True, "model": self.model, "embed_model_ready": False, "reason": None}

    @property
    def available(self):
        return True

    def chat(self, messages, schema=None, think=None, on_thinking=None, temperature=0.0, max_tokens=600):
        self.calls.append({"schema": schema, "messages": messages})
        if on_thinking and self.thinking:
            for tok in self.thinking.split(" "):
                on_thinking(tok + " ")
        if schema is not None and "intent" in schema.get("properties", {}):
            data = self.intent
        elif schema is not None:
            data = {"next": self.next_step, "why": "scripted"}
        else:
            data = None
        content = json.dumps(data) if data is not None else self.answer
        return {"content": content, "data": data, "thinking": self.thinking, "tokens_in": 10, "tokens_out": 5, "ms": 1.0}

    def embed(self, texts):
        raise RuntimeError("no embeddings in tests")
