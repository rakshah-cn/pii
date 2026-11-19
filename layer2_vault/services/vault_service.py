"""Tokenisation: detected PII in, masked text + safe references out.
Flow: validate -> check borrower/key state -> scoped blind-index lookup -> reuse or create (random token, AES-GCM encrypt) -> one
transaction -> masked text.  Any failure raises; the caller (gateway) must then NOT forward the raw text (fail closed)."""
import logging
import random
import re
import time

from ..core import encryption as enc
from ..core.blind_index import blind_index
from ..core.errors import KeyStateError, ValidationFailed, VaultDenied, VaultError, VaultUnavailable
from ..core.tokeniser import TYPE_RE, new_id, placeholder
from ..database.connection import ConflictError
from ..schemas.vault_schemas import ID_RE, LifecycleResponse, MetadataResponse, Mapping, TokenizeRequest, TokenizeResponse
from .context import VaultContext, log


class VaultService:
    def __init__(self, ctx: VaultContext):
        self.ctx = ctx

    # ------------------------------------------------------------------ keys
    def _dek_for(self, tx, borrower_id, create=True):
        c = self.ctx
        key = c.repo.active_key(tx, borrower_id)
        if key is None:
            latest = c.repo.latest_key(tx, borrower_id)
            # revoked or destroyed-by-erasure: never silently mint a new key. A key retired by RETENTION had no records left, so a fresh one is safe.
            if latest is not None and not (latest["key_status"] == "DESTROYED" and latest.get("destroy_reason") == "RETENTION"):
                raise KeyStateError("key_not_active")
            if not create:
                raise KeyStateError("key_not_active")
            key_id = new_id("key")
            dek = c.keys.new_dek()
            version, nonce, wrapped = c.keys.wrap(dek, borrower_id, key_id)
            c.repo.insert_key(tx, key_id, borrower_id, wrapped, nonce, version, now=c.clock())
            key = c.repo.active_key(tx, borrower_id)
            return key, dek
        return key, c.keys.unwrap(key["wrapped_dek"], key["wrap_nonce"], borrower_id, key["key_id"], key["kek_version"])

    # ------------------------------------------------------------------ core: resolve values to tokens inside ONE transaction
    def resolve(self, tx, borrower_id, conversation_id, purpose_id, groups):
        """groups: [{"type","value"}] -> [(token, vault_id)] in the same order. Reuses an ACTIVE mapping in the exact scope."""
        c = self.ctx
        if c.repo.ensure_borrower(tx, borrower_id) != "ACTIVE":
            raise VaultDenied("borrower_not_active")
        c.repo.lock_conversation(tx, conversation_id)
        key, dek = self._dek_for(tx, borrower_id)
        purpose = str(purpose_id).casefold()
        now = c.clock()
        ttl = c.settings.retention.ttl_for(purpose)
        expires_at = None if ttl is None else now + ttl
        for g in groups:
            if not g["value"] or len(g["value"]) > c.settings.max_value_len:
                raise ValidationFailed("bad_value")
        bkeys = {v: c.keys.blind_key(v) for v in c.keys.versions}                 # derived once per call (locals only; nothing is cached across calls)
        cur_v = c.keys.current_blind_version
        digs = [[(v, blind_index(bk, borrower_id, conversation_id, purpose, g["type"], g["value"])) for v, bk in bkeys.items()] for g in groups]
        hits = c.repo.find_by_digests_batch(tx, borrower_id, conversation_id, purpose, [(g["type"], v, d) for g, ds in zip(groups, digs) for v, d in ds])   # ONE query per 100 values
        out, pending, retired = [None] * len(groups), {}, set()
        for i, (g, ds) in enumerate(zip(groups, digs)):
            live = None
            for v, d in ds:                                                       # same preference order as before: first loaded version that has a LIVE hit
                h = hits.get((g["type"], v, d))
                if h is None:
                    continue
                if any(x is not None and x <= now for x in (h["m_expires"], h["r_expires"])):
                    if h["vault_id"] not in retired:
                        c.repo.expire_bundle(tx, h["vault_id"], now)               # expired but not yet cleaned up: retire it HERE, never reuse it
                        retired.add(h["vault_id"])
                    continue
                live = h
                break
            if live:
                out[i] = (live["token"], live["vault_id"])
            else:
                pending.setdefault((g["type"], dict(ds)[cur_v]), []).append(i)     # the same value twice in one request still gets ONE token
        if pending:
            counter = c.repo.allocate_nonces(tx, key["key_id"], len(pending), c.settings.key_usage_limit)   # one atomic reservation for the whole batch; raises key_usage_limit_reached
            seqs = c.repo.next_seqs(tx, conversation_id, [t for t, _ in pending])
            bundles = []
            for n, ((typ, cur), idxs) in enumerate(pending.items()):
                seqs[typ] += 1
                vault_id, token_id, token = new_id("v"), new_id("t"), placeholder(typ, seqs[typ])
                value = groups[idxs[0]]["value"]
                nonce, ct = enc.encrypt(dek, value, enc.record_aad(borrower_id, conversation_id, vault_id, typ, key["key_id"]), nonce=c.nonces.nonce(counter + n))
                bundles.append(({"vault_id": vault_id, "borrower_id": borrower_id, "conversation_id": conversation_id, "token_id": token_id, "pii_type": typ,
                                 "ciphertext": ct, "nonce": nonce, "key_id": key["key_id"], "purpose_id": purpose},
                                {"token_id": token_id, "token": token, "seq": seqs[typ]}, {"version": cur_v, "digest": cur}))
                for i in idxs:
                    out[i] = (token, vault_id)
            c.repo.insert_bundles(tx, bundles, now, expires_at)                    # executemany: three statements for the whole batch
        return out

    def _run(self, fn, op, borrower_id, request_id, caller):
        """Retry a unique-constraint race (two concurrent requests for the same value); map every other failure to a coded error."""
        for attempt in range(6):
            try:
                return fn()
            except ConflictError:
                time.sleep(random.uniform(0, 0.005 * (attempt + 1)))          # jittered back-off: a rare uniqueness race, not a busy loop
                continue
            except VaultError as e:
                self.ctx.record_failure(op, borrower_id, request_id, caller, e.code)
                raise
            except Exception as e:
                log.error("op=%s unexpected %s", op, type(e).__name__)
                self.ctx.record_failure(op, borrower_id, request_id, caller, "vault_unavailable")
                raise VaultUnavailable()
        raise VaultUnavailable("conflict_retries_exhausted")

    # ------------------------------------------------------------------ public: tokenize
    def tokenize(self, req: TokenizeRequest, caller, idempotency_key=None) -> TokenizeResponse:
        """idempotency_key (HTTP header Idempotency-Key): a retry with the same key + same request returns the first result without executing again."""
        c = self.ctx
        c.require(caller, "tokenize")
        if len(req.text) > c.settings.max_text_len or len(req.detected_entities) > c.settings.max_entities:
            raise ValidationFailed("request_too_large")
        try:
            c.engine.authorize(caller, "tokenize", req.borrower_id, req.purpose_id)          # operation + purpose + borrower scope + Layer-4 policy
        except VaultDenied as e:
            c.record_failure("TOKENIZE", req.borrower_id, req.request_id, caller, "denied" if e.code != "forbidden" else e.code)
            raise
        ents = sorted(req.detected_entities, key=lambda e: e.start)
        groups, members, joined = [], [], {}
        for e in ents:                                         # pieces of one entity share one token and one stored (joined) value
            if e.entity is not None and e.entity in joined:
                members.append(joined[e.entity]); continue
            groups.append({"type": e.type, "value": e.joined_value or e.value})
            members.append(len(groups) - 1)
            if e.entity is not None:
                joined[e.entity] = len(groups) - 1

        payload = req.model_dump(exclude={"request_id"})              # a retry may carry a new request_id: the CONTENT identifies the request

        def work():
            with c.db.transaction() as tx:
                if idempotency_key is not None:
                    state, ref = c.idempotency.claim(tx, idempotency_key, "TOKENIZE", caller.name, payload,
                                                     still_valid=lambda r: len(c.repo.live_vault_ids(tx, [x[1] for x in r["groups"]], c.clock())) == len({x[1] for x in r["groups"]}))
                    if state == "REPLAY":
                        return [tuple(x) for x in ref["groups"]]
                res = self.resolve(tx, req.borrower_id, req.conversation_id, req.purpose_id, groups)
                c.repo.log_op(tx, "TOKENIZE", req.borrower_id, req.request_id, caller.name, "SUCCESS")
                if idempotency_key is not None:
                    c.idempotency.complete(tx, idempotency_key, "TOKENIZE", caller.name, {"groups": res})      # references only: tokens and vault ids
                return res
        res = self._run(work, "TOKENIZE", req.borrower_id, req.request_id, caller)
        masked, mappings = req.text, []
        for e, gi in sorted(zip(ents, members), key=lambda x: -x[0].start):
            token, vid = res[gi]
            masked = masked[:e.start] + token + masked[e.end:]
        for e, gi in zip(ents, members):
            mappings.append(Mapping(token=res[gi][0], vault_id=res[gi][1], pii_type=e.type, start=e.start, end=e.end))
        c.emit(op="tokenize", request_id=req.request_id, caller=caller.name, borrower=req.borrower_id, status="success", entities=len(ents))
        return TokenizeResponse(request_id=req.request_id, masked_text=masked, mappings=mappings)

    def tokenize_values(self, borrower_id, conversation_id, purpose_id, items, caller, request_id="internal", idempotency_key=None):
        """Internal (no text): [(type, value)] -> [(token, vault_id)], ONE transaction for the whole batch. Used by the gateway.
        idempotency_key: same key + same batch returns the first result without executing again; a different batch under the same key is rejected."""
        c = self.ctx
        c.require(caller, "tokenize")
        # same checks as the HTTP path: id alphabet, type name, and the permission decision (unavailable => deny)
        if not all(isinstance(x, str) and re.match(ID_RE, x) for x in (borrower_id, conversation_id, purpose_id)) or not all(TYPE_RE.match(t) for t, _ in items):
            raise ValidationFailed("invalid_request")
        try:
            c.engine.authorize(caller, "tokenize", borrower_id, purpose_id)
        except VaultDenied as e:
            c.record_failure("TOKENIZE", borrower_id, request_id, caller, "denied" if e.code != "forbidden" else e.code)
            raise
        groups = [{"type": t, "value": v} for t, v in items]
        payload = {"b": borrower_id, "c": conversation_id, "p": str(purpose_id).casefold(), "items": [list(i) for i in items]}

        def work():
            with c.db.transaction() as tx:
                if idempotency_key is not None:
                    state, ref = c.idempotency.claim(tx, idempotency_key, "TOKENIZE", caller.name, payload,
                                                     still_valid=lambda r: len(c.repo.live_vault_ids(tx, [x[1] for x in r["groups"]], c.clock())) == len({x[1] for x in r["groups"]}))
                    if state == "REPLAY":
                        return [tuple(x) for x in ref["groups"]]
                res = self.resolve(tx, borrower_id, conversation_id, purpose_id, groups)
                c.repo.log_op(tx, "TOKENIZE", borrower_id, request_id, caller.name, "SUCCESS")
                if idempotency_key is not None:
                    c.idempotency.complete(tx, idempotency_key, "TOKENIZE", caller.name, {"groups": res})
                return res
        return self._run(work, "TOKENIZE", borrower_id, request_id, caller)

    # ------------------------------------------------------------------ metadata (never plaintext)
    def get_metadata(self, vault_id, caller) -> MetadataResponse:
        c = self.ctx
        c.require(caller, "metadata")
        with c.db.transaction() as tx:
            r = c.repo.record_by_vault_id(tx, vault_id)
            if not r or not caller.covers_borrower(r["borrower_id"]):
                raise VaultDenied("not_found")                                  # outside this caller's borrower scope looks exactly like a missing record
            k = c.repo.latest_key(tx, r["borrower_id"])
        expired = r["status"] == "ACTIVE" and r["expires_at"] is not None and r["expires_at"] <= c.clock()      # report what rehydration would enforce
        return MetadataResponse(vault_id=r["vault_id"], token=r["token"], pii_type=r["pii_type"], status="EXPIRED" if expired else r["status"],
                                mapping_status="EXPIRED" if expired and r["mapping_status"] == "ACTIVE" else r["mapping_status"], expires_at=r["expires_at"],
                                purpose_id=r["purpose_id"], borrower_id=r["borrower_id"], conversation_id=r["conversation_id"],
                                key_status=k["key_status"] if k else None, created_at=r["created_at"])

    def lifecycle(self, vault_id, caller):
        """Where is this record in its life? Distinguishes: never existed / live / expired / pending deletion / partially failed deletion / deleted.
        Uses the PII-free tombstone for deleted records. (Capability 'metadata'.)"""
        c = self.ctx
        c.require(caller, "metadata")
        with c.db.transaction() as tx:
            r = c.repo.record_by_vault_id(tx, vault_id)
            if r and not caller.covers_borrower(r["borrower_id"]):
                raise VaultDenied("not_found")
            if r:
                jobs = c.repo.job_statuses(tx, r["borrower_id"], r["conversation_id"])           # jobs that cover THIS record (borrower scope, or its conversation)
                if r["status"] == "PENDING_ERASURE" or jobs & {"PENDING", "IN_PROGRESS", "PARTIAL_FAILURE"}:
                    state = "PARTIALLY_FAILED" if "PARTIAL_FAILURE" in jobs else "PENDING_DELETION"
                elif r["status"] == "EXPIRED" or (r["expires_at"] is not None and r["expires_at"] <= c.clock()):
                    state = "EXPIRED"
                else:
                    state = "ACTIVE"
                return LifecycleResponse(vault_id=vault_id, lifecycle=state)
            t = c.repo.tombstone(tx, vault_id)
        if t:
            return LifecycleResponse(vault_id=vault_id, lifecycle=t["deletion_status"], deleted_at=t["deleted_at"], reason=t["reason"])
        return LifecycleResponse(vault_id=vault_id, lifecycle="NEVER_EXISTED")

    def list_for_borrower(self, borrower_id, caller):
        self.ctx.engine.authorize(caller, "metadata", borrower_id)
        with self.ctx.db.transaction() as tx:
            return [{k: v for k, v in r.items() if k not in ("ciphertext", "nonce")} for r in self.ctx.repo.records_for(tx, borrower_id)]

    def health(self):
        ok_db = self.ctx.db.ping()
        try:
            self.ctx.keys.kek(); self.ctx.keys.blind_key(); ok_keys = True
        except Exception:
            ok_keys = False
        role_ok = None
        if ok_db and self.ctx.db.kind == "postgres":
            try:
                role_ok = not self.ctx.db.role_info()["superuser"]
            except Exception:
                role_ok = None
        return {"status": "ok" if ok_db and ok_keys and role_ok is not False else "degraded", "database": ok_db, "key_material": ok_keys, "db_role_safe": role_ok}
