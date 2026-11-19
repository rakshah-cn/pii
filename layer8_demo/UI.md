# Demo console (Layer 8 front end)

Run from the project root:

    .venv/bin/python -m layer8_demo.server        # then open http://127.0.0.1:8808
    DEMO_PORT=9000 .venv/bin/python -m layer8_demo.server   # another port

Startup takes about 8 s (it loads and warms the detector). Needs `uvicorn` in the venv (`pip install uvicorn`).

## What it is
A thin web layer over the real layers. `server.py` calls the layers' own code (L1 detector, L2 vault, L3 gateway, L4 `guarded_write`, L5 registry, L6 agent, L7 chain); `web/` is plain HTML/CSS/JS with no CDN, so it works offline.
One stage per layer (L0-L8), each with: what it does, a live panel, measured numbers, a "run this layer's tests" button, and its known limits. The bar at the bottom sends one message through the whole stack; every stage shows its own slice of that message.

## Things to know
- One global session at a time. Page load or "New" starts a clean session (fresh vault, key, stores, audit chain).
- Stores are MOCK JSONL files under `data/stores/ui/<session>/`. All values are fake.
- LLM is a local stub by default. The header switch calls Bedrock Nova Pro with TOKENISED text only. A live model can invent facts (it has no loan data): use the stub when you need a deterministic demo.
- `/api/message` returns the raw text you just typed so the page can highlight it. The production gateway never does.
- "Detector blind" replaces Layer 1 with one that finds nothing, to show the audit net. It catches phone / email / PAN-like values and CANNOT catch names (documented limit; the page shows it).
- Layer 5 lineage and the Layer 4 `guarded_write` are wired in by `server.py`, not by the gateway itself.
