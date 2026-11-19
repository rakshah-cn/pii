"""Layer 2: encrypted token vault. Placeholder <-> real value.

DESIGN (envelope encryption, separate key store)
    master key --HKDF--> KEK
    KEK wraps one random 256-bit DATA KEY per owner (owner = borrower_id, or "conv:<conversation_id>")
    every value = AES-256-GCM(data key, nonce, AAD = JSON[owner, conversation_id, label, token, vault_id, key_id])
    blind index = HMAC(random blind key, JSON[conversation, owner, label, value])   (dedupe without storing values;
    the blind key is random, stored wrapped in the key store, and survives master-key rotation)
Tokens are readable counters (<PHONE_1>) minted by the vault, never derived from the value.

PERSISTENCE (optional `path`) = THREE separate files:
    <path>              rows: ciphertext + metadata ONLY. No key material of any kind.
    <path>.keys         the KEY STORE: wrapped per-owner data keys + wrapped blind key (+ KEK epoch). Rewritten (0600,
                        atomic replace) whenever an owner's key is created or destroyed.
    <path>.audit.jsonl  hash-chained access events (0600), no raw values.
Master key: VAULT_MASTER_KEY (hex/base64, >=16 bytes) or the `master_key` argument; else a SESSION key is generated
(data then cannot be read after the process ends). The key is never logged.

WHAT CRYPTO-SHREDDING (destroy_borrower) ACTUALLY GUARANTEES
    + the live process and the current key store hold no key for the borrower; all their rows are deleted;
    + a copy/backup of the DATA file (rows) cannot be decrypted without the borrower's key: restoring it onto the
      current key store fails (VaultError), master key or not;
    + after rotate_master_key(), a pre-shred backup of the WHOLE state (rows + key store) cannot be opened with the
      NEW master key (the old wrapped keys cannot be unwrapped).
  NOT guaranteed (out of scope for this hackathon build, needs KMS/HSM + backup policy):
    - an attacker holding BOTH a pre-shred backup of data+key store AND the OLD master key can still decrypt that backup;
      mitigation = rotate_master_key() after a shred and destroy the old master key;
    - deleting a file is not forensic-grade on SSDs/journaled filesystems; backups must be kept out of the key store's reach.

AUTHORIZATION / AUDIT
    Every decrypt-path call (get, scan_text, decrypt_for_erasure) must present a role that is in the capability allowlist
    for that action, is rate limited per (role, action), and writes an ATTEMPT event BEFORE decrypting and an OUTCOME event
    before the plaintext is returned (failed/tampered decrypts included). If the audit write fails the call raises and no
    plaintext is returned (fail closed). Events are hash-chained; verify_audit() checks the chain.
Any failure to encrypt/decrypt/persist raises VaultError: the gateway turns that into a BLOCK.

STATUS: hackathon-grade. Master key is env-held (not KMS/HSM). Rate limits are per process.
"""
import base64
import collections
import hashlib
import hmac
import json
import os
import re
import threading
import time
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

DEFAULT_CAPS = {                      # role -> actions it may perform
    "rehydration": {"get"},
    "audit": {"scan_text"},
    "erasure": {"decrypt_for_erasure", "destroy_borrower", "destroy"},
}
DEFAULT_LIMITS = {                    # action -> (max calls, window seconds) per (role, action)
    "decrypt_for_erasure": (200, 60.0),
    "get": (100000, 60.0),
    "scan_text": (100000, 60.0),
}
BLIND_OWNER = "__blind__"


from .core.errors import VaultError  # noqa: E402  (shared with the modular vault so callers catch ONE type; "Vault cannot encrypt/decrypt/persist: callers must fail closed")


class RateLimited(VaultError):
    pass


def _norm(value: str) -> str:
    return re.sub(r"[\s-]", "", value).casefold()


def _decode_key(text: str) -> bytes:
    text = text.strip()
    if re.fullmatch(r"[0-9a-fA-F]{32,}", text):
        return bytes.fromhex(text)
    try:
        key = base64.b64decode(text, validate=True)
    except Exception as e:
        raise VaultError("VAULT_MASTER_KEY must be hex or base64") from e
    if len(key) < 16:
        raise VaultError("VAULT_MASTER_KEY too short (need >= 16 bytes)")
    return key


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def _aad(*parts) -> bytes:
    """Unambiguous encoding of the associated data (JSON list, no separator tricks)."""
    return json.dumps(list(parts), separators=(",", ":"), ensure_ascii=True).encode()


def _derive_kek(master_key: bytes) -> bytes:
    return HKDF(hashes.SHA256(), 32, None, b"te03-vault-kek").derive(master_key)


def _write_private(path: str, text: str):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Vault:
    def __init__(self, master_key: bytes | None = None, path: str | None = None, capabilities=None,
                 strict_roles: bool = False, rate_limits=None):
        self._lock = threading.RLock()
        if master_key is None and os.environ.get("VAULT_MASTER_KEY"):
            master_key = _decode_key(os.environ["VAULT_MASTER_KEY"])
        self.session_key = master_key is None            # True => data unreadable after this process
        if master_key is None:
            master_key = os.urandom(32)
        if len(master_key) < 16:
            raise VaultError("master key too short (need >= 16 bytes)")
        self._master = master_key
        self._kek = _derive_kek(master_key)
        self._bidx = os.urandom(32)                       # blind-index key (random; persisted wrapped in the key store)
        self._keys = {}        # owner -> {"key_id", "nonce"(bytes), "wrapped"(bytes)}   (the KEY STORE, in memory)
        self._blind_rec = None
        self._rows = {}        # conversation_id -> {token: row}
        self._counts = {}      # (conversation_id, label) -> n
        self._index = {}       # (conversation_id, label, blind) -> token   (blind includes the owner)
        self._destroyed = set()
        self._audit = []
        self._audit_head = "genesis"
        self._calls = {}       # (role, action) -> deque of timestamps
        self.audit_degraded = False
        self.caps = {r: set(a) for r, a in (capabilities or DEFAULT_CAPS).items()}
        self.strict_roles = strict_roles
        self.limits = dict(DEFAULT_LIMITS, **(rate_limits or {}))
        self.path = path
        self.keys_path = path + ".keys" if path else None
        self.audit_path = path + ".audit.jsonl" if path else None
        self._pending_persist = False
        if path and os.path.exists(path or "") or (self.keys_path and os.path.exists(self.keys_path)):
            self._load()
        else:
            self._seal_blind_key()
        self._resume_audit_chain()

    # ------------------------------------------------------------------ keys & crypto
    def _wrap(self, owner, key_id, secret: bytes):
        nonce = os.urandom(12)
        return nonce, AESGCM(self._kek).encrypt(nonce, secret, _aad("wrap", owner, key_id))

    def _unwrap(self, owner, rec):
        try:
            return AESGCM(self._kek).decrypt(rec["nonce"], rec["wrapped"], _aad("wrap", owner, rec["key_id"]))
        except InvalidTag as e:
            raise VaultError("cannot unwrap key (wrong master key, rotated master key, or tampered key store)") from e

    def _seal_blind_key(self):
        kid = "b-" + uuid.uuid4().hex[:12]
        nonce, wrapped = self._wrap(BLIND_OWNER, kid, self._bidx)
        self._blind_rec = {"key_id": kid, "nonce": nonce, "wrapped": wrapped}

    def _data_key(self, owner, create=False):
        rec = self._keys.get(owner)
        if rec is None:
            if not create:
                return None, None
            key_id = "k-" + uuid.uuid4().hex[:12]
            nonce, wrapped = self._wrap(owner, key_id, os.urandom(32))
            rec = {"key_id": key_id, "nonce": nonce, "wrapped": wrapped}
            self._keys[owner] = rec
            self._destroyed.discard(owner)
        return self._unwrap(owner, rec), rec["key_id"]

    def _encrypt(self, owner, conversation_id, label, token, vault_id, raw):
        dk, key_id = self._data_key(owner, create=True)
        nonce = os.urandom(12)
        ct = AESGCM(dk).encrypt(nonce, raw.encode("utf-8"), _aad("row", owner, conversation_id, label, token, vault_id, key_id))
        return _b64(ct), _b64(nonce), key_id

    def _decrypt_row(self, row):
        dk, cur_key_id = self._data_key(row["owner"])
        if dk is None:
            raise VaultError("data key destroyed: value is unrecoverable")
        try:
            return AESGCM(dk).decrypt(_unb64(row["nonce"]), _unb64(row["ct"]),
                                      _aad("row", row["owner"], row["conversation_id"], row["label"], row["token"],
                                           row["vault_id"], row["key_id"])).decode("utf-8")
        except InvalidTag as e:
            raise VaultError("ciphertext failed authentication (tampered, swapped or wrong key)") from e

    def _blind(self, conversation_id, owner, label, raw):
        msg = _aad(conversation_id, owner, label, _norm(raw))          # the OWNER is part of the dedupe key
        return hmac.new(self._bidx, msg, hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------ authorization, rate limit, audit
    def _authorize(self, role, action, **ctx):
        if role is None:
            if self.strict_roles:
                self._event(action=action, role=None, outcome="denied", **ctx)
                raise PermissionError("an explicit role is required")
            role = "?"
        if action not in self.caps.get(role, set()):
            self._event(action=action, role=role, outcome="denied", **ctx)
            raise PermissionError(f"role {role!r} may not perform {action!r}")
        max_calls, window = self.limits.get(action, (10 ** 9, 1.0))
        dq = self._calls.setdefault((role, action), collections.deque())
        now = time.monotonic()
        while dq and dq[0] < now - window:
            dq.popleft()
        if len(dq) >= max_calls:
            self._event(action=action, role=role, outcome="rate_limited", **ctx)
            raise RateLimited(f"rate limit exceeded for {role}/{action}")
        dq.append(now)

    def _event(self, fatal=False, **kw):
        """Hash-chained, raw-free audit event. fatal=True: if it cannot be written, RAISE (decrypt paths)."""
        body = {"ts": time.time(), "prev": self._audit_head, **kw}
        body["hash"] = hashlib.sha256((self._audit_head + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()
        failed = None
        if self.audit_path:
            try:
                fd = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                    os.write(fd, (json.dumps(body, default=str) + "\n").encode())
                finally:
                    os.close(fd)
            except OSError as e:
                failed = e
                self.audit_degraded = True
        self._audit.append(body)
        self._audit_head = body["hash"]
        if failed and fatal:
            raise VaultError(f"audit write failed, refusing the operation: {failed}")

    def _resume_audit_chain(self):
        if self.audit_path and os.path.exists(self.audit_path):
            try:
                last = open(self.audit_path, encoding="utf-8").read().splitlines()[-1]
                self._audit_head = json.loads(last)["hash"]
            except (OSError, ValueError, IndexError, KeyError):
                self._audit_head = "unverified:" + hashlib.sha256(b"unreadable audit tail").hexdigest()[:16]

    @staticmethod
    def _chain_ok(events) -> bool:
        prev = None
        for e in events:
            body = {k: v for k, v in e.items() if k != "hash"}
            if (prev is not None and e.get("prev") != prev) or e.get("hash") != hashlib.sha256((str(e.get("prev")) + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest():
                return False
            prev = e["hash"]
        return True

    def verify_audit_file(self, path=None) -> bool:
        """Re-computes the hash chain from the audit FILE on disk (survives restarts; detects edits to any event).
        Limit: deleting the LAST events leaves a valid shorter chain; anchor audit_head() somewhere outside to detect that."""
        path = path or self.audit_path
        if not path or not os.path.exists(path):
            return True
        try:
            events = [json.loads(l) for l in open(path, encoding="utf-8").read().splitlines() if l.strip()]
        except (OSError, ValueError):
            return False
        return self._chain_ok(events)

    def audit_head(self) -> str:
        return self._audit_head

    def verify_audit(self) -> bool:
        """True only if BOTH the in-memory chain and the audit file on disk re-compute correctly."""
        with self._lock:
            mem = self._chain_ok(self._audit)
        return mem and self.verify_audit_file()

    def access_log(self):
        with self._lock:
            return [dict(e) for e in self._audit]

    # ------------------------------------------------------------------ public API (the contract)
    def put(self, conversation_id, label, raw, borrower_id=None, purpose="service", consent_id=None, source=None):
        if not isinstance(raw, str) or not raw:
            raise VaultError("nothing to store")
        owner = borrower_id or f"conv:{conversation_id}"
        with self._lock:
            blind = self._blind(conversation_id, owner, label, raw)
            tok = self._index.get((conversation_id, label, blind))
            if tok:                                        # same value, same owner -> same token
                if source:
                    self._rows[conversation_id][tok]["sources"].append(source)
                return tok
            n = self._counts.get((conversation_id, label), 0) + 1
            token = f"<{label}_{n}>"
            had_key, vault_id = owner in self._keys, uuid.uuid4().hex
            try:
                ct, nonce, key_id = self._encrypt(owner, conversation_id, label, token, vault_id, raw)
            except VaultError:
                if not had_key:
                    self._keys.pop(owner, None)
                raise
            row = {"vault_id": vault_id, "owner": owner, "conversation_id": conversation_id, "label": label,
                   "token": token, "ct": ct, "nonce": nonce, "key_id": key_id, "purpose": purpose,
                   "consent_id": consent_id, "created": time.time(), "blind": blind,
                   "sources": [source] if source else []}
            old_count = self._counts.get((conversation_id, label))
            self._counts[(conversation_id, label)] = n
            self._rows.setdefault(conversation_id, {})[token] = row
            self._index[(conversation_id, label, blind)] = token
            try:
                self._persist()
            except Exception:                              # roll back: a retry must not trust unpersisted state
                self._rows[conversation_id].pop(token, None)
                if not self._rows[conversation_id]:
                    del self._rows[conversation_id]
                self._index.pop((conversation_id, label, blind), None)
                if old_count is None:
                    self._counts.pop((conversation_id, label), None)
                else:
                    self._counts[(conversation_id, label)] = old_count
                if not had_key:
                    self._keys.pop(owner, None)
                raise
            self._event(action="put", role="gateway", conversation_id=conversation_id, token=token, owner=owner)
            return token

    def get(self, conversation_id, token, role="rehydration"):
        self._authorize(role, "get", conversation_id=conversation_id, token=token)
        with self._lock:
            self._event(fatal=True, action="get", role=role, conversation_id=conversation_id, token=token, outcome="attempt")
            outcome, row = "error", None
            try:
                row = self._rows.get(conversation_id, {}).get(token)
                if row is None:
                    outcome = "miss"
                    return None
                raw = self._decrypt_row(row)
                outcome = "ok"
                return raw
            except VaultError:
                outcome = "failed"
                raise
            finally:                                       # raises (discarding plaintext) if the audit cannot be written
                self._event(fatal=True, action="get", role=role, conversation_id=conversation_id, token=token,
                            owner=row["owner"] if row else None, purpose=row["purpose"] if row else None, outcome=outcome)

    def tokens(self, conversation_id):
        with self._lock:
            return list(self._rows.get(conversation_id, {}))

    def _owner_rows(self, owner):
        return [r for conv in self._rows.values() for r in conv.values() if r["owner"] == owner]

    def destroy(self, conversation_id, role="erasure"):
        self._authorize(role, "destroy", conversation_id=conversation_id)
        with self._lock:
            rows = self._rows.pop(conversation_id, {})
            idx = {k: v for k, v in self._index.items() if k[0] == conversation_id}
            cnt = {k: v for k, v in self._counts.items() if k[0] == conversation_id}
            for k in idx:
                del self._index[k]
            for k in cnt:
                del self._counts[k]
            dropped_keys = {}
            for owner in {r["owner"] for r in rows.values()}:       # conversation-scoped keys die with the conversation
                if owner.startswith("conv:") and not self._owner_rows(owner):
                    dropped_keys[owner] = self._keys.pop(owner, None)
            self._persist_shred(lambda: self._restore(rows, idx, cnt, dropped_keys, set()))
            self._event(action="destroy", role=role, conversation_id=conversation_id, entries=len(rows))
            return len(rows)

    def entries_for_borrower(self, borrower_id):
        with self._lock:
            return [{"token": r["token"], "label": r["label"], "conversation_id": r["conversation_id"],
                     "created": r["created"], "purpose": r["purpose"], "consent_id": r["consent_id"]}
                    for r in self._owner_rows(borrower_id)]

    def has_key(self, borrower_id):
        with self._lock:
            return borrower_id in self._keys

    def decrypt_for_erasure(self, borrower_id, role="erasure"):
        """Raw values for the erasure agent's SEALED sweep only. The agent itself must never receive them."""
        self._authorize(role, "decrypt_for_erasure", owner=borrower_id, purpose="erasure")
        with self._lock:
            self._event(fatal=True, action="decrypt_for_erasure", role=role, purpose="erasure", owner=borrower_id, outcome="attempt")
            outcome, count = "error", 0
            try:
                rows = self._owner_rows(borrower_id)
                if not rows and borrower_id in self._destroyed:
                    outcome = "key_destroyed"
                    raise VaultError("data key destroyed: nothing can be decrypted")
                out = list(dict.fromkeys(self._decrypt_row(r) for r in rows))
                count, outcome = len(out), "ok"
                return out
            except VaultError:
                if outcome == "error":
                    outcome = "failed"
                raise
            finally:
                self._event(fatal=True, action="decrypt_for_erasure", role=role, purpose="erasure", owner=borrower_id,
                            outcome=outcome, count=count)

    def destroy_borrower(self, borrower_id, role="erasure"):
        """Crypto-shred: destroy the borrower's data key (key store rewritten FIRST) AND delete every row they own."""
        self._authorize(role, "destroy_borrower", owner=borrower_id, purpose="erasure")
        with self._lock:
            removed, idx = {}, {}
            for cid in list(self._rows):
                for tok in [t for t, r in self._rows[cid].items() if r["owner"] == borrower_id]:
                    row = self._rows[cid].pop(tok)
                    removed.setdefault(cid, {})[tok] = row
                    k = (cid, row["label"], row["blind"])
                    if self._index.pop(k, None) is not None:
                        idx[k] = tok
                if not self._rows[cid]:
                    del self._rows[cid]
            key = self._keys.pop(borrower_id, None)
            was_destroyed = borrower_id in self._destroyed
            self._destroyed.add(borrower_id)
            n = sum(len(v) for v in removed.values())
            self._persist_shred(lambda: (self._restore(removed, idx, {}, {borrower_id: key} if key else {}, set()),
                                         self._destroyed.discard(borrower_id) if not was_destroyed else None))
            self._event(action="destroy_borrower", role=role, purpose="erasure", owner=borrower_id,
                        entries=n, key_destroyed=key is not None)
            return {"entries_deleted": n, "key_destroyed": key is not None}

    def _restore(self, rows, idx, cnt, keys, _unused):
        for cid, toks in rows.items():
            self._rows.setdefault(cid, {}).update(toks)
        self._index.update(idx)
        self._counts.update(cnt)
        for o, k in keys.items():
            if k is not None:
                self._keys[o] = k

    # ------------------------------------------------------------------ gateway helpers
    def extend(self, conversation_id, token, piece=None, joined=None):
        """A number said in pieces: keep ONE token. Replace its value with `joined` (full value) or append `piece`."""
        with self._lock:
            row = self._rows.get(conversation_id, {}).get(token)
            if row is None:
                raise VaultError("unknown token")
            cur = self._decrypt_row(row)
            new = joined if joined else (cur if not piece else f"{cur} {piece}")
            if new == cur:
                return token
            ct, nonce, key_id = self._encrypt(row["owner"], conversation_id, row["label"], token, row["vault_id"], new)
            new_blind = self._blind(conversation_id, row["owner"], row["label"], new)
            old = {k: row[k] for k in ("ct", "nonce", "key_id", "blind")}
            old_idx_key = (conversation_id, row["label"], row["blind"])
            row.update(ct=ct, nonce=nonce, key_id=key_id, blind=new_blind)       # mutate only AFTER the new ciphertext exists
            self._index.pop(old_idx_key, None)
            self._index[(conversation_id, row["label"], new_blind)] = token
            try:
                self._persist()
            except Exception:
                row.update(old)
                self._index.pop((conversation_id, row["label"], new_blind), None)
                self._index[old_idx_key] = token
                raise
            self._event(action="extend", role="gateway", conversation_id=conversation_id, token=token)
            return token

    def scan_text(self, conversation_id, text, role="audit", min_len=4):
        """Audit helper: where does any value held for this conversation still appear in `text`?
        Decryption happens INSIDE the vault; only (token, start, end) leave it, never a raw value."""
        self._authorize(role, "scan_text", conversation_id=conversation_id)
        hits = []
        with self._lock:
            self._event(fatal=True, action="scan_text", role=role, conversation_id=conversation_id, outcome="attempt")
            outcome = "error"
            try:
                digit_pos = [(i, ch) for i, ch in enumerate(text) if ch.isdigit()]
                stream = "".join(ch for _, ch in digit_pos)
                for tok, row in self._rows.get(conversation_id, {}).items():
                    v = self._decrypt_row(row)
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
                outcome = "ok"
                return hits
            except VaultError:
                outcome = "failed"
                raise
            finally:
                self._event(fatal=True, action="scan_text", role=role, conversation_id=conversation_id,
                            outcome=outcome, hits=len(hits))

    def rotate_master_key(self, new_master_key: bytes | None = None):
        """Re-wrap every key under a NEW master key and rewrite the key store. Call after a shred and destroy the old
        key: a pre-shred backup of rows+keys can then no longer be opened with the current master key.
        Returns the new key only if it was generated here (store it; it is not persisted)."""
        generated = new_master_key is None
        new_master_key = new_master_key or os.urandom(32)
        if len(new_master_key) < 16:
            raise VaultError("master key too short (need >= 16 bytes)")
        with self._lock:
            old_keys, old_blind, old_kek, old_master = dict(self._keys), self._blind_rec, self._kek, self._master
            try:
                secrets = {o: self._unwrap(o, r) for o, r in self._keys.items()}
                self._kek, self._master = _derive_kek(new_master_key), new_master_key
                for o, secret in secrets.items():
                    nonce, wrapped = self._wrap(o, self._keys[o]["key_id"], secret)
                    self._keys[o] = {"key_id": self._keys[o]["key_id"], "nonce": nonce, "wrapped": wrapped}
                self._seal_blind_key()
                self._persist()
            except Exception:
                self._keys, self._blind_rec, self._kek, self._master = old_keys, old_blind, old_kek, old_master
                raise
            self._event(fatal=True, action="rotate_master_key", role="admin", outcome="ok")
        return new_master_key if generated else None

    # ------------------------------------------------------------------ persistence
    def _keystore_json(self):
        return json.dumps({"v": 2,
                           "keys": {o: {"key_id": k["key_id"], "nonce": _b64(k["nonce"]), "wrapped": _b64(k["wrapped"])}
                                    for o, k in self._keys.items()},
                           "blind": {"key_id": self._blind_rec["key_id"], "nonce": _b64(self._blind_rec["nonce"]),
                                     "wrapped": _b64(self._blind_rec["wrapped"])}})

    def _data_json(self):
        return json.dumps({"v": 2, "rows": [r for conv in self._rows.values() for r in conv.values()],
                           "counts": [[c, l, n] for (c, l), n in self._counts.items()],
                           "destroyed": sorted(self._destroyed)})

    def _persist(self):
        """Key store first, then rows. Raises VaultError on failure."""
        if not self.path:
            return
        try:
            _write_private(self.keys_path, self._keystore_json())
        except OSError as e:
            raise VaultError(f"cannot persist key store: {e}") from e
        try:
            _write_private(self.path, self._data_json())
            self._pending_persist = False
        except OSError as e:
            self._pending_persist = True
            raise VaultError(f"cannot persist vault rows: {e}") from e

    def _persist_shred(self, rollback):
        """Persist a destructive change. If the KEY STORE write fails nothing was destroyed on disk: roll back memory.
        If only the rows write fails, the keys ARE gone on disk: stay shredded in memory (never resurrect a key) and
        retry on the next persist."""
        if not self.path:
            return
        try:
            _write_private(self.keys_path, self._keystore_json())
        except OSError as e:
            rollback()
            raise VaultError(f"cannot persist key store, shred rolled back: {e}") from e
        try:
            _write_private(self.path, self._data_json())
            self._pending_persist = False
        except OSError as e:
            self._pending_persist = True
            raise VaultError(f"keys destroyed but the rows file could not be rewritten yet (will retry): {e}") from e

    def _load(self):
        try:
            data = json.load(open(self.path, encoding="utf-8")) if os.path.exists(self.path) else {"rows": []}
            ks = json.load(open(self.keys_path, encoding="utf-8")) if os.path.exists(self.keys_path) else None
        except (OSError, ValueError) as e:
            raise VaultError(f"cannot read vault files: {e}") from e
        if ks is None:
            if data.get("rows"):
                raise VaultError("key store missing: rows cannot be decrypted")
            self._seal_blind_key()
            return
        self._keys = {o: {"key_id": k["key_id"], "nonce": _unb64(k["nonce"]), "wrapped": _unb64(k["wrapped"])}
                      for o, k in ks.get("keys", {}).items()}
        b = ks["blind"]
        self._blind_rec = {"key_id": b["key_id"], "nonce": _unb64(b["nonce"]), "wrapped": _unb64(b["wrapped"])}
        self._bidx = self._unwrap(BLIND_OWNER, self._blind_rec)       # wrong/rotated master key fails HERE
        for owner, rec in self._keys.items():                          # every key must unwrap with this master key
            self._unwrap(owner, rec)
        for r in data.get("rows", []):
            rec = self._keys.get(r["owner"])
            if rec is not None and rec["key_id"] != r["key_id"]:
                raise VaultError("key id mismatch between rows and key store (files from different states)")
            self._rows.setdefault(r["conversation_id"], {})[r["token"]] = r
            self._index[(r["conversation_id"], r["label"], r["blind"])] = r["token"]
        self._counts = {(c, l): n for c, l, n in data.get("counts", [])}
        self._destroyed = set(data.get("destroyed", []))
