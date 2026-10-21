"""The supervisor's transparency trace.

A trace is an ordered list of steps that is streamed to the UI while the supervisor works
(the "thinking" panel under the prompt) and stored with the answer for later audit.

Step kinds
  perceive  what state the supervisor loaded (profile, goal, beliefs)
  reason    a deterministic policy judgement, with the rule and thresholds it used
  decide    a choice among fixed options; `source` says whether the model or a rule chose
  tool      a skill call with its arguments and a summary of the result
  model     the language model's own reasoning text (shown verbatim, marked unverified)
  check     a validation of model output against evidence (passed / rejected and why)
  answer    the final reply
Each step carries `evidence`: the concrete records (event ids, recipe ids, numbers) that
support it, so a user can follow every claim back to data.
"""
import time
import uuid


class Trace:
    def __init__(self, sink=None):
        self.steps = []
        self.sink = sink
        self.t0 = time.perf_counter()

    def _push(self, kind, payload):
        if self.sink:
            self.sink({"type": kind, **payload})

    def step(self, kind, title, detail=None, evidence=None, source="policy", status="done", data=None):
        s = {"id": uuid.uuid4().hex[:8], "kind": kind, "title": title, "detail": detail,
             "evidence": evidence, "source": source, "status": status, "data": data,
             "t_ms": round((time.perf_counter() - self.t0) * 1000, 1)}
        self.steps.append(s)
        self._push("step", {"step": s})
        return s

    def start(self, kind, title, detail=None, source="policy", data=None):
        """Open a running step (rendered with the live cursor) and return it for `finish`."""
        return self.step(kind, title, detail, source=source, status="running", data=data)

    def finish(self, s, detail=None, evidence=None, status="done", title=None, data=None):
        s.update({"status": status, "duration_ms": round((time.perf_counter() - self.t0) * 1000 - s["t_ms"], 1)})
        if detail is not None:
            s["detail"] = detail
        if evidence is not None:
            s["evidence"] = evidence
        if title is not None:
            s["title"] = title
        if data is not None:
            s["data"] = data
        self._push("step_update", {"step": s})
        return s

    def model_token(self, step, text):
        """Stream raw model reasoning into a `model` step."""
        step["detail"] = (step.get("detail") or "") + text
        self._push("model_token", {"id": step["id"], "text": text})

    def elapsed_ms(self):
        return round((time.perf_counter() - self.t0) * 1000, 1)
