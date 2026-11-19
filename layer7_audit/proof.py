"""Human-readable proof for one erasure request. States exactly what was checked, and what that does NOT prove."""


def render_proof(rec, chain_ok=True, chain_reason="chain_intact", head=None):
    L = []
    L.append("=" * 66)
    L.append(f"ERASURE PROOF   request {rec['request_id']}   seq #{rec['seq']}")
    L.append(f"borrower (keyed hash): {rec['borrower_hash'][:16]}...   (raw id not stored)")
    L.append(f"status: {rec['status']}   passes: {rec['passes']}   duration: {rec['duration_s']}s")
    L.append("-" * 66)
    L.append(f"{'store':<26}{'found':>7}{'deleted':>9}{'verified zero':>15}")
    for s in rec["stores"]:
        L.append(f"{s['store']:<26}{s['found']:>7}{s['deleted']:>9}{('yes' if s['verified_zero'] else 'NO'):>15}")
    v = rec["vault"]
    L.append(f"vault: entries deleted {v['entries_deleted']}, key destroyed: {v['key_destroyed']}, "
             f"entries remaining: {v.get('entries_remaining', '?')}")
    if rec.get("probabilistic"):
        L.append(f"probabilistic deletions (embedding similarity >= threshold): {rec['probabilistic']} "
                 f"-- verified only at that threshold")
    if rec.get("unresolved"):
        L.append(f"UNRESOLVED (not deleted, needs a human): {len(rec['unresolved'])}")
        for u in rec["unresolved"][:10]:
            L.append(f"   - {u['store']}:{u['id']}  reason={u['reason']}")
    L.append("-" * 66)
    L.append(f"checks RUN (every pass): {', '.join(rec.get('methods_run', []))}")
    L.append(f"checks that FOUND copies: {', '.join(rec.get('methods', []))}")
    L.append(f"chain: {'INTACT' if chain_ok else 'BROKEN'} ({chain_reason})   hash: {rec['hash'][:16]}...")
    if head:
        L.append(f"chain head to anchor externally: {head[:16]}...")
    L.append("-" * 66)
    L.append("WHAT THIS PROVES: none of the listed discovery methods found any remaining copy after deletion.")
    L.append("WHAT IT DOES NOT PROVE: that no copy exists in a store or encoding the methods cannot see.")
    L.append("=" * 66)
    return "\n".join(L)
