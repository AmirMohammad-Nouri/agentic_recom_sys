"""Supervisor Agent for preference conflict detection and transparent reasoning traces."""
import json
import urllib.request
from . import CRITERIA, CRITERION_LABELS


class SupervisorAgent:
    def __init__(self, kg, rag, ollama_url="http://localhost:11434", model="qwen3:1.7b"):
        self.kg = kg
        self.rag = rag
        self.ollama_url = ollama_url
        self.model = model

    def analyze(self, user_id, declared, goal, learner):
        """Perform step-by-step reasoning over goals, learner choices, KG, and RAG context."""
        trace = []

        # Step 1: Goal vs Revealed Preference Inspection
        trace.append({
            "step": "1. Stated Goal Inspection",
            "evidence": f"Current Saved Goal Weights: {goal['weights']}",
            "thought": "Loading saved user goal weights to set baseline expectations for evaluation."
        })

        # Step 2: Extract Bayesian Learner Beliefs
        inferred = learner.choice.inferred() if learner.choice else None
        discrepancies = learner.choice.discrepancy(goal["weights"]) if learner.choice else {}

        conflicts = []
        if inferred:
            for k in CRITERIA:
                disc = discrepancies.get(k, {})
                if disc.get("prob_beyond_delta", 0.0) > 0.4:
                    conflicts.append({
                        "criterion": k,
                        "label": CRITERION_LABELS[k],
                        "stated": goal["weights"][k],
                        "inferred": round(inferred[k]["mean"], 3),
                        "direction": disc.get("direction")
                    })

        trace.append({
            "step": "2. Preference Discrepancy Analysis",
            "evidence": f"Discrepancies found: {len(conflicts)}",
            "thought": (
                f"Identified {len(conflicts)} significant preference shifts between stated goals and revealed choice history."
                if conflicts else "Revealed choices align well with stated goal weights."
            )
        })

        # Step 3: Knowledge Graph History Inspection
        kg_history = self.kg.query_user_conflicts(user_id)
        trace.append({
            "step": "3. Knowledge Graph Evidence Retrieval",
            "evidence": f"Retrieved {len(kg_history)} decision nodes from Knowledge Graph.",
            "thought": "Checking if past choice sequences (e.g. choosing higher cost/time items) explain preference divergence."
        })

        # Step 4: Domain RAG Retrieval
        top_conflict_text = conflicts[0]["label"] if conflicts else "healthy balanced diet"
        rag_context = self.rag.search(top_conflict_text, top_k=2)
        trace.append({
            "step": "4. Domain RAG Context Grounding",
            "evidence": [doc["title"] for doc in rag_context],
            "thought": f"Grounded analysis with retrieved domain documents on {top_conflict_text}."
        })

        # Step 5: Formulate Supervisor Proposal
        proposal = None
        if conflicts:
            c = conflicts[0]
            proposed_weights = dict(goal["weights"])
            # Propose adjusting weight towards inferred mean
            proposed_weights[c["criterion"]] = round((c["stated"] + c["inferred"]) / 2.0, 2)
            # Renormalize
            total = sum(proposed_weights.values())
            proposed_weights = {k: round(v / total, 3) for k, v in proposed_weights.items()}

            proposal = {
                "reason": f"Your recent actions show you prioritize {c['label']} ({c['inferred']}) more than your goal ({c['stated']}).",
                "proposed_weights": proposed_weights,
                "conflict_criterion": c["criterion"]
            }

            trace.append({
                "step": "5. Goal Adjustment Proposal",
                "evidence": f"Conflict detected in {c['label']}.",
                "thought": f"Proposing non-intrusive weight updates for {c['label']}. Saved goal is NOT automatically overwritten."
            })

        # Optional LLM Synthesis (if Ollama Qwen local server is running)
        llm_summary = self._call_ollama_synthesis(trace, proposal)

        return {
            "reasoning_trace": trace,
            "conflicts": conflicts,
            "proposal": proposal,
            "llm_summary": llm_summary
        }

    def _call_ollama_synthesis(self, trace, proposal):
        """Call local Ollama Qwen model if available; gracefully fall back to template."""
        prompt = (
            "You are a helpful, concise Supervisor AI for a food recommender system.\n"
            f"Reasoning trace: {json.dumps(trace)}\n"
            f"Proposal: {json.dumps(proposal)}\n"
            "Summarize your reasoning in 2 direct sentences for the user."
        )
        data = json.dumps({"model": self.model, "prompt": prompt, "stream": False}).encode("utf-8")
        req = urllib.request.Request(f"{self.ollama_url}/api/generate", data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return res.get("response")
        except Exception:
            # Local model offline / fallback
            if proposal:
                return f"Supervisor Note: {proposal['reason']} Consider reviewing your priority weights."
            return "Supervisor Note: Your preferences are well aligned with your active recommendation settings."