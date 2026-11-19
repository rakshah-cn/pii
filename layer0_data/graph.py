r"""LangGraph loop:  generate -> evaluate --approved--> END
                                    \--needs_improvement--> optimize -> evaluate (max 3 rounds)

generate  : build a row (skeleton + code-made fake values)
evaluate  : deterministic checks (src/checks.py) -> approved / needs_improvement
optimize  : fix the row. Stub = re-roll values with a new seed. With an LLM, rewrite
            the skeleton given the failure reason (hook: `rewrite_fn`).
"""
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from . import checks, convo

MAX_ROUNDS = 3


class State(TypedDict, total=False):
    cell: dict
    seed: int
    row: dict
    errors: list
    rounds: int
    status: str            # approved | needs_improvement | discarded
    history: list


def build_graph(skeleton_fn=convo.stub_skeleton, rewrite_fn=None, seen=None):
    seen = seen if seen is not None else set()

    def generate(s: State):
        row = convo.build_row(s["cell"], s["seed"], skeleton_fn)
        return {"row": row, "rounds": 0, "history": []}

    def evaluate(s: State):
        errs = checks.check_row(s["row"], seen_hashes=None)   # dedup after approval
        ok, why = checks.llm_judge_naturalness(s["row"])
        if not ok:
            errs.append("unnatural:" + why)
        if not errs:
            h = checks.check_row(s["row"], seen_hashes=seen)  # registers hash, flags dup
            errs = [e for e in h if e == "duplicate"]
        hist = s.get("history", []) + ([errs] if errs else [])
        if not errs:
            return {"errors": [], "status": "approved", "history": hist}
        if s["rounds"] >= MAX_ROUNDS:
            return {"errors": errs, "status": "discarded", "history": hist}
        return {"errors": errs, "status": "needs_improvement", "history": hist}

    def optimize(s: State):
        skel = rewrite_fn(s["row"]["skeleton"], s["errors"]) if rewrite_fn else None
        # LLM rewrite keeps the same seed (same fake values, so the placeholders still match);
        # without a rewrite we just re-roll everything with a new seed
        seed = s["seed"] if skel else s["seed"] + 1000 * (s["rounds"] + 1)
        row = convo.build_row(s["cell"], seed, skeleton_fn, skeleton=skel)
        return {"row": row, "rounds": s["rounds"] + 1}

    def route(s: State):
        return "optimize" if s["status"] == "needs_improvement" else END

    g = StateGraph(State)
    g.add_node("generate", generate)
    g.add_node("evaluate", evaluate)
    g.add_node("optimize", optimize)
    g.add_edge(START, "generate")
    g.add_edge("generate", "evaluate")
    g.add_conditional_edges("evaluate", route, {"optimize": "optimize", END: END})
    g.add_edge("optimize", "evaluate")
    return g.compile()
