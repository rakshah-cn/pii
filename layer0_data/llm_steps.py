"""LLM steps for dataset quality (Bedrock). Only SKELETONS go to the model: turns with {SLOT}
placeholders. No fake values, no real data.   judge() = evaluate step, rewrite() = optimize step."""
import json
import re
from collections import Counter

from common import llm

MODEL = "apac.amazon.nova-pro-v1:0"   # Claude models need the Anthropic use-case form first
SLOT = re.compile(r"\{[A-Z0-9_]+\}")

JUDGE_SYS = """You review synthetic customer-conversation skeletons for a loan-collections company in India. They are used
to test a PII detector. Three kinds exist: phone-call transcripts ([Agent]/[Customer] turns, noisy, repetitive),
chat messages (informal, lower-case, abbreviations, emojis) and prompts to an LLM ([User] single turn that embeds
borrower data). Text may be English or Indian languages (Hindi, Marathi, Kannada, Tamil, in native script or
Roman letters), and mixing languages inside one conversation is NORMAL.
Words in {BRACES} are placeholders filled later. IMPORTANT conventions, never flag these:
  * {X_P1} and {X_P2} are the two halves of ONE number said/typed in two consecutive turns. This is intended.
  * the same placeholder appearing twice means the customer repeated the same value.
  * {DATE} vs {DATE2}, or two different amounts, are fine. {ORG} is a company name.
  * 'Hello', 'Hmm', cut-off sentences, repeated turns, unresolved or abruptly ending calls are NORMAL.
Do not judge tone, politeness, or whether the agent solved the issue.
Reply ONLY with JSON: {"verdict":"ok"|"fix","reason":"<one short sentence>"}.
Say "fix" only for clear STRUCTURAL problems: a placeholder used in a role that makes no sense (e.g. an amount where a
phone number is requested), agent and customer roles swapped, the answer to a question is a placeholder of the
wrong type, or a conversation that is incoherent. When unsure, say "ok"."""

REWRITE_SYS = """You fix synthetic conversation skeletons (call, chat or prompt). Keep the style, language mix and length.
You MUST keep every {PLACEHOLDER} exactly as it appears (same names, same count), keep {X_P1}/{X_P2} in two separate
consecutive turns, and not add new placeholders. Fix only the problem described. Speakers are 'Agent', 'Customer'
or 'User'. Reply ONLY with a JSON list of [speaker, text] pairs."""

_CLIENT = None


def _ask(prompt, system, max_tokens, temperature):
    global _CLIENT
    if _CLIENT is None:
        import boto3
        from botocore.config import Config
        _CLIENT = boto3.client("bedrock-runtime", region_name=llm.REGION,
                               config=Config(retries={"max_attempts": 12, "mode": "adaptive"}, read_timeout=60))
    r = _CLIENT.converse(modelId=MODEL, system=[{"text": system}], messages=[{"role": "user", "content": [{"text": prompt}]}],
                         inferenceConfig={"maxTokens": max_tokens, "temperature": temperature})
    return r["output"]["message"]["content"][0]["text"]


def _fmt(skeleton):
    return "\n".join(f"[{s}]: {t}" for s, t in skeleton)


def _json(txt):
    m = re.search(r"[\[{].*[\]}]", txt, re.S)
    return json.loads(m.group(0)) if m else None


def judge(skeleton):
    # no try/except on the API call on purpose: if the judge cannot run, fail loudly instead of approving
    out = _json(_ask(_fmt(skeleton), JUDGE_SYS, 150, 0))
    if not out or "verdict" not in out:
        raise RuntimeError("judge returned unparseable output")
    return out["verdict"] == "ok", out.get("reason", "")


def rewrite(skeleton, errors):
    reason = "; ".join(e for e in errors if e.startswith("unnatural"))[:300] or "; ".join(errors)
    try:
        out = _json(_ask(f"Problem: {reason}\n\nSkeleton:\n{_fmt(skeleton)}", REWRITE_SYS, 1200, 0.5))
        new = [(str(s), str(t)) for s, t in out]
    except Exception:
        return None
    ok_roles = all(s in ("Agent", "Customer", "User") for s, _ in new)
    same = Counter(SLOT.findall(_fmt(skeleton))) == Counter(SLOT.findall(_fmt(new)))
    return new if (ok_roles and same and new) else None     # reject rewrites that lose/add placeholders
