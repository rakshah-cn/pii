"""Layer 5: lineage registry (the family tree of where a borrower's data ended up).

borrower -> conversation -> log record -> vector chunk -> embedding -> eval item -> vault entry
Refs look like  'log:L12', 'chunk:V7', 'emb:V7', 'eval:E3', 'vault:<PHONE_1>@c_B0001_1'.

The registry is deliberately INCOMPLETE in practice (pipelines forget to register things), so it also keeps
INVERTED INDEXES (by token, by conversation, by name-hash) that the erasure agent uses to find records the
tree never saw. It holds only ids and keyed hashes, never raw personal values.

Optimisation pass (19 Nov): O(1) per-borrower lookup (`borrower_refs`), purpose/consent on nodes (`ref_metadata`, from Layer 4),
reverse maps so `forget_borrower` leaves no residue, atomic JSON save, HMAC key from LINEAGE_HMAC_KEY, a lock, and
`register_write` (the one call the gateway makes after a successful Layer-4 guarded_write).
"""
import hashlib
import hmac
import json
import os
import re
import threading
import warnings
from collections import defaultdict

KINDS = ("log", "chunk", "emb", "eval", "vault")
DEV_KEY = b"lineage-demo-key-change-me"             # tests / demos ONLY; production sets LINEAGE_HMAC_KEY
STORE_KIND = {"logs": "log", "vector_store": "chunk", "eval_sets": "eval"}     # Layer 4 store name -> ref kind
TOKEN_RE = re.compile(r"<[A-Z_]+_\d+>")             # same shape as the gateway's vault placeholders
FORMAT_VERSION = 2


def tokens_in(masked_text):
    """Vault placeholders in already-masked text (placeholders carry no personal data)."""
    return sorted(set(TOKEN_RE.findall(masked_text or "")))


def ref(kind, ident):
    return f"{kind}:{ident}"


def split_ref(r):
    k, _, i = r.partition(":")
    return k, i


def _resolve_key(key):
    """explicit key > env LINEAGE_HMAC_KEY > dev fallback. Returns (bytes, using_dev_key)."""
    if key is not None:
        return (key.encode() if isinstance(key, str) else bytes(key)), key == DEV_KEY
    env = os.environ.get("LINEAGE_HMAC_KEY")
    if env:
        return env.encode(), False
    return DEV_KEY, True


class LineageRegistry:
    def __init__(self, key=None):
        self.key, self.using_dev_key = _resolve_key(key)
        if self.using_dev_key:
            warnings.warn("LineageRegistry is using the DEV HMAC key: set LINEAGE_HMAC_KEY outside tests/demos",
                          RuntimeWarning, stacklevel=2)
        self._lock = threading.RLock()
        self.borrower_refs = {}                    # borrower -> {ref}            O(1) known_for (no scan of all refs)
        self.ref_metadata = {}                     # ref -> {"purpose":..., "consent_id":...}   from Layer 4
        self.ref_tokens = defaultdict(set)         # ref -> {token}      reverse of token_index (clean erasure)
        self.ref_conv = defaultdict(set)           # ref -> {conversation_id}   reverse of conv_index
        self.parents = defaultdict(set)            # child ref -> {parent ref}  reverse of children
        self.conversations = defaultdict(set)      # borrower -> {conversation_id}
        self.conv_owner = {}                       # conversation_id -> borrower
        self.owner = {}                            # ref -> borrower
        self.children = defaultdict(set)           # parent ref -> {child ref}
        self.relation = {}                         # (parent, child) -> relation text
        self.token_index = defaultdict(set)        # token -> {ref}
        self.conv_index = defaultdict(set)         # conversation_id -> {ref}
        self.name_hash = {}                        # borrower -> keyed hash of their name
        self.name_index = defaultdict(set)         # keyed name hash -> {borrower}

    # ---- keyed hashing (names are never stored raw) ----
    def hash_value(self, raw):
        norm = " ".join(str(raw).split()).casefold()
        return hmac.new(self.key, norm.encode(), hashlib.sha256).hexdigest()

    # ---- building the tree ----
    def add_borrower(self, borrower_id, name=None):
        self.conversations.setdefault(borrower_id, set())
        self.borrower_refs.setdefault(borrower_id, set())
        if name:
            h = self.hash_value(name)
            self.name_hash[borrower_id] = h
            self.name_index[h].add(borrower_id)

    def add_conversation(self, borrower_id, conversation_id):
        self.conversations[borrower_id].add(conversation_id)
        self.conv_owner[conversation_id] = borrower_id

    def register(self, borrower_id, r, parent=None, relation="derived_from", conversation_id=None, token=None,
                 tokens=None, purpose=None, consent_id=None):
        """Record that `r` belongs to `borrower_id`. `tokens`/`token`: vault placeholders appearing in it.
        `purpose`/`consent_id` (Layer 4 tag) are stored on the node; omitting them never erases earlier values.
        A ref already owned by a DIFFERENT borrower raises (silently re-owning could make erasure delete or keep the wrong data)."""
        with self._lock:
            prev = self.owner.get(r)
            if prev is not None and prev != borrower_id:
                raise ValueError(f"ref_owned_by_another_borrower:{r}")
            self.owner[r] = borrower_id
            self.borrower_refs.setdefault(borrower_id, set()).add(r)
            if purpose is not None or consent_id is not None:
                md = self.ref_metadata.setdefault(r, {"purpose": None, "consent_id": None})
                if purpose is not None:
                    md["purpose"] = purpose
                if consent_id is not None:
                    md["consent_id"] = consent_id
            if parent:
                self.link(parent, r, relation)
            if conversation_id:
                self.conv_index[conversation_id].add(r)
                self.ref_conv[r].add(conversation_id)
            for t in ([token] if token else []) + list(tokens or ()):
                self.token_index[t].add(r)
                self.ref_tokens[r].add(t)

    def link(self, parent, child, relation="derived_from"):
        with self._lock:
            self.children[parent].add(child)
            self.parents[child].add(parent)
            self.relation[(parent, child)] = relation

    def register_write(self, borrower_id, store, record_id, masked_text=None, conversation_id=None, purpose=None,
                       consent_id=None, parent=None, relation="derived_from"):
        """The gateway's hook: call AFTER Layer 4 guarded_write returned True. Maps the Layer 4 store name to a ref kind,
        extracts the vault placeholders from the MASKED text (the text itself is not kept) and registers the node with its
        purpose and consent. Returns the ref string."""
        kind = STORE_KIND.get(store)
        if kind is None:
            raise ValueError(f"unknown_store:{store}")
        r = ref(kind, record_id)
        self.register(borrower_id, r, parent=parent, relation=relation, conversation_id=conversation_id,
                      tokens=tokens_in(masked_text), purpose=purpose, consent_id=consent_id)
        return r

    # ---- queries ----
    def descendants(self, r):
        seen, stack = set(), [r]
        while stack:
            for c in self.children.get(stack.pop(), ()):
                if c not in seen:
                    seen.add(c); stack.append(c)
        return seen

    def known_for(self, borrower_id, purpose=None):
        """Everything the registry knows for a borrower, grouped by kind (ids only). O(this borrower's refs), not O(all refs).
        purpose=X keeps refs tagged X PLUS refs with no purpose recorded (unknown could be X, and erasure must over-include, not under-include)."""
        with self._lock:
            roots = set(self.borrower_refs.get(borrower_id, ()))
            allrefs = set(roots)
            for r in roots:
                allrefs |= self.descendants(r)
            if purpose is not None:
                allrefs = {r for r in allrefs if self.ref_metadata.get(r, {}).get("purpose") in (None, purpose)}
        out = {k: [] for k in KINDS}
        for r in sorted(allrefs):
            k, i = split_ref(r)
            out.setdefault(k, []).append(i)
        return out

    def conversations_of(self, borrower_id):
        return sorted(self.conversations.get(borrower_id, ()))

    def borrower_of(self, conversation_id):
        return self.conv_owner.get(conversation_id)

    def refs_for_token(self, token):
        return sorted(self.token_index.get(token, ()))

    def refs_for_conversation(self, conversation_id):
        return sorted(self.conv_index.get(conversation_id, ()))

    def borrowers_sharing_name(self, borrower_id):
        h = self.name_hash.get(borrower_id)
        return sorted(self.name_index.get(h, set()) - {borrower_id}) if h else []

    def name_hash_of(self, borrower_id):
        return self.name_hash.get(borrower_id)

    def forget_borrower(self, borrower_id):
        """After erasure: drop this borrower's tree AND every index entry that points at it (children, parents, relation,
        token_index, conv_index, metadata, name index), so the registry no longer links to - or mentions - deleted data.
        Refs linked below this borrower's refs but owned by ANOTHER borrower are kept (only unlinked)."""
        with self._lock:
            roots = set(self.borrower_refs.get(borrower_id, ()))
            doomed = set(roots)
            for r in roots:
                doomed |= self.descendants(r)
            doomed = {r for r in doomed if self.owner.get(r, borrower_id) == borrower_id}   # unowned linked refs go too
            for r in doomed:
                self.owner.pop(r, None)
                self.ref_metadata.pop(r, None)
                for t in self.ref_tokens.pop(r, ()):
                    self._discard(self.token_index, t, r)
                for c in self.ref_conv.pop(r, ()):
                    self._discard(self.conv_index, c, r)
                for p in self.parents.pop(r, ()):
                    self._discard(self.children, p, r)
                    self.relation.pop((p, r), None)
                for ch in self.children.pop(r, ()):
                    self._discard(self.parents, ch, r)
                    self.relation.pop((r, ch), None)
            for c in self.conversations.pop(borrower_id, set()):
                self.conv_owner.pop(c, None)
                for r in self.conv_index.pop(c, ()):
                    self._discard(self.ref_conv, r, c)
            h = self.name_hash.pop(borrower_id, None)
            if h and h in self.name_index:
                self.name_index[h].discard(borrower_id)
                if not self.name_index[h]:
                    del self.name_index[h]
            self.borrower_refs.pop(borrower_id, None)

    @staticmethod
    def _discard(index, key, value):
        s = index.get(key)
        if s is not None:
            s.discard(value)
            if not s:
                del index[key]

    # ---- persistence ----
    def key_fingerprint(self):
        """Short fingerprint of the HMAC key (not reversible), saved with the file so a load with the wrong key is caught."""
        return hmac.new(self.key, b"lineage-key-check", hashlib.sha256).hexdigest()[:16]

    def save(self, path):
        """Atomic: write a temp file, fsync, then os.replace. A crash mid-write leaves the previous file intact."""
        with self._lock:
            d = {"version": FORMAT_VERSION, "key_fp": self.key_fingerprint(),
                 "conversations": {b: sorted(c) for b, c in self.conversations.items()},
                 "owner": dict(self.owner), "children": {p: sorted(c) for p, c in self.children.items()},
                 "relation": [[p, c, r] for (p, c), r in self.relation.items()],
                 "token_index": {t: sorted(r) for t, r in self.token_index.items()},
                 "conv_index": {c: sorted(r) for c, r in self.conv_index.items()},
                 "name_hash": dict(self.name_hash), "ref_metadata": {r: dict(m) for r, m in self.ref_metadata.items()}}
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    @classmethod
    def load(cls, path, key=None):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        r = cls(key)
        if d.get("key_fp") and d["key_fp"] != r.key_fingerprint():
            raise ValueError("lineage_key_mismatch: this file was saved with a different HMAC key (name-collision hashes would be wrong)")
        for b, cs in d["conversations"].items():
            r.conversations.setdefault(b, set())
            r.borrower_refs.setdefault(b, set())
            for c in cs:
                r.add_conversation(b, c)
        r.owner = dict(d["owner"])
        for ref_, b in r.owner.items():
            r.borrower_refs.setdefault(b, set()).add(ref_)
        for p, cs in d["children"].items():
            r.children[p] = set(cs)
            for c in cs:
                r.parents[c].add(p)
        r.relation = {(p, c): rel for p, c, rel in d["relation"]}
        for t, rs in d["token_index"].items():
            r.token_index[t] = set(rs)
            for x in rs:
                r.ref_tokens[x].add(t)
        for c, rs in d["conv_index"].items():
            r.conv_index[c] = set(rs)
            for x in rs:
                r.ref_conv[x].add(c)
        r.ref_metadata = {k: dict(v) for k, v in d.get("ref_metadata", {}).items()}
        r.name_hash = dict(d["name_hash"])
        for b, h in r.name_hash.items():
            r.name_index[h].add(b)
            r.borrower_refs.setdefault(b, set())
        return r
