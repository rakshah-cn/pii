"""Operations used INSIDE the trust boundary by the gateway (Layer 3) and the erasure agent (Layer 6).
Plaintext is decrypted in here; what leaves is a placeholder, a position, a category or a bool (capability 'erasure_sweep').
No call returns a borrower's values. Every decrypt writes an audit 'attempt' event first; if that cannot be written, it is refused."""
import re

from ..core import encryption as enc
from ..core.blind_index import blind_index
from ..core.errors import KeyStateError, ValidationFailed, VaultDenied, VaultUnavailable
from ..database.connection import ConflictError
from ..core.sealed_match import SealedMaterial
from .context import VaultContext


LIVE_STATUSES = ("ACTIVE", "EXPIRED")      # records whose ciphertext still exists and may still be echoed by a downstream copy


class InternalOps:
    def __init__(self, ctx: VaultContext):
        self.ctx = ctx

    def _dek(self, tx, borrower_id):
        c = self.ctx
        key = c.repo.active_key(tx, borrower_id)
        if key is None or c.repo.borrower_state(tx, borrower_id) != "ACTIVE":
            raise KeyStateError("key_not_active")
        return key, c.keys.unwrap(key["wrapped_dek"], key["wrap_nonce"], borrower_id, key["key_id"], key["kek_version"])

    def _plain(self, r, dek):
        return enc.decrypt(dek, r["nonce"], r["ciphertext"], enc.record_aad(r["borrower_id"], r["conversation_id"], r["vault_id"], r["pii_type"], r["key_id"]))

    # ---- a number said in pieces keeps ONE token: replace its stored value by the joined value
    def extend(self, conversation_id, token, caller, piece=None, joined=None):
        c = self.ctx
        c.require(caller, "tokenize")
        with c.db.transaction() as tx:
            row = c.repo.mapping_with_record(tx, conversation_id, token)
            now = c.clock()
            if (not row or not caller.covers_borrower(row["borrower_id"]) or row["record_status"] != "ACTIVE" or row["mapping_status"] != "ACTIVE"
                    or any(x is not None and x <= now for x in (row["mapping_expires_at"], row["record_expires_at"]))):
                raise VaultDenied("token_not_found")
            try:
                c.engine.authorize(caller, "tokenize", row["borrower_id"], row["purpose_id"], policy=False)      # the caller's PURPOSE scope too, not only its borrower scope (extend rewrites the record)
            except VaultDenied:
                raise VaultDenied("token_not_found")                                                              # same answer as an unknown token: a caller outside its scope learns nothing
            key, dek = self._dek(tx, row["borrower_id"])
            cur = self._plain(row, dek)
            new = joined if joined else (cur if not piece else f"{cur} {piece}")
            if new == cur:
                return token
            if len(new) > c.settings.max_value_len:
                raise ValidationFailed("bad_value")                                                               # the same limit tokenise enforces
            counter = c.repo.allocate_nonces(tx, key["key_id"], 1, c.settings.key_usage_limit)     # every encryption under a DEK takes a counter, not only first inserts
            nonce, ct = enc.encrypt(dek, new, enc.record_aad(row["borrower_id"], row["conversation_id"], row["vault_id"], row["pii_type"], row["key_id"]), nonce=c.nonces.nonce(counter))
            c.repo.update_cipher(tx, row["vault_id"], ct, nonce)
            bv = c.persisted_versions(tx)[1]
            digest = blind_index(c.keys.blind_key(bv), row["borrower_id"], row["conversation_id"], row["purpose_id"], row["pii_type"], new)
            try:
                c.repo.replace_index(tx, row["vault_id"], bv, digest)
            except ConflictError:
                raise VaultDenied("value_collision")
            c.repo.log_op(tx, "EXTEND", row["borrower_id"], None, caller.name, "SUCCESS", vault_id=row["vault_id"])
        c.emit(op="extend", caller=caller.name, status="success")
        return token

    # ---- where does a value we hold still appear in `text`?  only (token, start, end) leave the vault
    @staticmethod
    def _hits(rows_values, text, min_len):
        hits, digit_pos = [], [(i, ch) for i, ch in enumerate(text) if ch.isdigit()]
        stream = "".join(ch for _, ch in digit_pos)
        for tok, v in rows_values:
            dv = re.sub(r"\D", "", v)
            if len(dv) >= 6 and len(dv) >= 0.8 * len(re.sub(r"[\s-]", "", v)):
                j = stream.find(dv)
                while j != -1:
                    hits.append((tok, digit_pos[j][0], digit_pos[j + len(dv) - 1][0] + 1))
                    j = stream.find(dv, j + len(dv))
            elif len(v) >= min_len:
                j = text.find(v)
                while j != -1:
                    hits.append((tok, j, j + len(v)))
                    j = text.find(v, j + len(v))
        return hits

    def scan_text(self, conversation_id, text, caller, min_len=4):
        c = self.ctx
        c.require(caller, "scan")
        c.emit(must=True, op="scan_attempt", caller=caller.name, conversation=conversation_id)
        values = []
        with c.db.transaction() as tx:
            by_borrower = {}
            for b in c.repo.borrowers_in_conversation(tx, conversation_id):
                if not caller.covers_borrower(b):
                    continue                                                    # only borrowers inside this caller's scope are scanned
                try:
                    by_borrower[b] = self._dek(tx, b)
                except KeyStateError:
                    continue
            for b, (key, dek) in by_borrower.items():
                for r in c.repo.records_for(tx, b, conversation_id, with_cipher=True):
                    if r["status"] in LIVE_STATUSES and r["key_id"] == key["key_id"]:      # expired-but-not-yet-deleted data still exists: scan for it too
                        values.append((r["token"], self._plain(r, dek)))
        hits = self._hits(values, text, min_len)
        c.emit(op="scan_outcome", caller=caller.name, conversation=conversation_id, hits=len(hits))
        return hits

    # ---- sealed erasure sweep (Layer 6). Plaintext NEVER leaves the vault: there is no call that returns the borrower's values.
    # Layer 6 sends text (or vectors); it gets back a category per text, a score per vector row, or a bool.
    def _sealed(self, borrower_id, caller, op, n):
        c = self.ctx
        c.engine.authorize(caller, "erasure_sweep", borrower_id)
        c.emit(must=True, op=f"{op}_attempt", caller=caller.name, borrower=borrower_id, items=n)    # the 'attempt' event is written BEFORE any decrypt; if it cannot be written the call is refused
        with c.db.transaction() as tx:
            try:
                key, dek = self._dek(tx, borrower_id)
            except KeyStateError:
                return SealedMaterial([])
            return SealedMaterial([self._plain(r, dek) for r in c.repo.records_for(tx, borrower_id, with_cipher=True)
                                   if r["status"] in LIVE_STATUSES and r["key_id"] == key["key_id"]])    # expired-but-not-yet-deleted data still exists: scan for it too

    def erasure_scan(self, borrower_id, texts, caller, mode="values"):
        """One category per text: 'strong' | 'address' | 'name_full' | 'name_single' | None. mode='collapsed' ignores formatting
        (long identifiers only, so only 'strong' or None). Nothing else leaves the vault."""
        mat = self._sealed(borrower_id, caller, "erasure_scan", len(texts))
        out = [mat.classify_collapsed(t) if mode == "collapsed" else mat.classify(t) for t in texts]
        self.ctx.emit(op="erasure_scan_outcome", caller=caller.name, borrower=borrower_id, mode=mode, matched=sum(1 for x in out if x))
        return out

    def erasure_probe(self, borrower_id, rows, embed_fn, caller, threshold=0.0):
        """Nearest-neighbour probe. `rows` = [(row_id, vector)]. The query vector is built HERE from the borrower's own values with
        `embed_fn` and never returned. Returns [(row_id, cosine_score)] for rows scoring at least `threshold`."""
        mat = self._sealed(borrower_id, caller, "erasure_probe", len(rows))
        if not mat or not rows:
            return []
        import numpy as np
        q = np.asarray(embed_fn(mat.probe_text()), dtype=np.float32)
        out = []
        ok = [(rid, vec) for rid, vec in rows if len(vec) == len(q)]            # a row of another dimension cannot be compared
        if ok:
            scores = np.asarray([v for _, v in ok], dtype=np.float32) @ q
            out = [(rid, float(sc)) for (rid, _), sc in zip(ok, scores) if sc >= threshold]
        self.ctx.emit(op="erasure_probe_outcome", caller=caller.name, borrower=borrower_id, matched=len(out))
        return out

    def find_unmapped_copies(self, borrower_id, search_corpus, caller):
        """True if `search_corpus` contains this borrower's raw PII (phone / ID code / address / full name). A single bool."""
        return any(k in ("strong", "address", "name_full") for k in self.erasure_scan(borrower_id, [search_corpus], caller))

    def match_borrower(self, borrower_id, texts, caller, min_len=4):
        """The safer alternative to decrypt_for_erasure: which of these texts still contain any of the borrower's values?
        Returns [[(token, start, end)], ...] per text. No plaintext leaves the vault."""
        c = self.ctx
        c.engine.authorize(caller, "erasure_sweep", borrower_id)
        c.emit(must=True, op="erasure_match_attempt", caller=caller.name, borrower=borrower_id, texts=len(texts))
        with c.db.transaction() as tx:
            try:
                key, dek = self._dek(tx, borrower_id)
            except KeyStateError:
                return [[] for _ in texts]
            values = [(r["token"], self._plain(r, dek)) for r in c.repo.records_for(tx, borrower_id, with_cipher=True) if r["status"] in LIVE_STATUSES and r["key_id"] == key["key_id"]]
        return [self._hits(values, t, min_len) for t in texts]
