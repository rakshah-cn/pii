"""Transcript persistence + lineage: what the gateway does with a finished message so that erasure can find it later.

For every message that passed the whole pipeline (status ok / degraded) the gateway can write ONE tokenised log record and register it:
  1. Layer 4  guarded_write('logs', record, ...)   purpose limitation + audit event (write_allowed / write_rejected / write_failed, keyed borrower hash, never the body)
  2. Layer 5  registry.register_write(...)          only AFTER the write was allowed: borrower -> conversation -> log record, with the vault tokens it mentions, purpose and consent id
The record holds the MASKED incoming text and the tokenised reply - never rehydrated text, and never the raw borrower id (it is removed before the store sees it: the audit event
carries a keyed hash instead). A rejected / failed write is not registered. Persistence problems never block the conversation: they are counted in `stats`.
The registry file is what Layer 6 loads, so it is saved every `save_every` records (default 1) and on flush()."""
import threading
import time
from collections import Counter

from layer4_consent.guard import guarded_write
from layer4_consent.write_guard import new_tag


class _StripBorrowerId:
    """Wraps the real store adapter: the borrower id is needed by the guard (for the keyed audit hash) but must not be written into the store."""
    def __init__(self, adapter):
        self.adapter = adapter

    def put(self, record):
        rec = dict(record)
        rec.pop("borrower_id", None)
        tag = rec.get("consent")
        if tag is not None:
            d = tag.to_dict() if hasattr(tag, "to_dict") else dict(tag)
            d.pop("borrower_id", None)
            rec["consent"] = d
        self.adapter.put(rec)


class LogPersistence:
    def __init__(self, adapter, registry, audit_logger, registry_path=None, store="logs", save_every=1):
        self.adapter, self._store_adapter = adapter, _StripBorrowerId(adapter)
        self.registry, self.audit, self.registry_path, self.store, self.save_every = registry, audit_logger, registry_path, store, save_every
        self.stats = Counter()
        self._lock, self._written, self._since_save = threading.Lock(), set(), 0

    def record(self, request_id, conversation_id, borrower_id, purpose, consent_id, masked_text, reply_masked=None):
        """Returns True when the record was written AND registered. Never raises for a policy rejection (it returns False)."""
        rid = f"log-{request_id}"
        with self._lock:
            if rid in self._written:
                self.stats["duplicate_skipped"] += 1
                return False                                                  # the same request id is never written twice
            tag = new_tag(purpose, consent_id=consent_id, explicit=False, note="gateway transcript", at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), borrower_id=borrower_id)
            rec = {"id": rid, "conversation_id": conversation_id, "borrower_id": borrower_id, "text": masked_text, "reply_masked": reply_masked, "consent": tag}
            if not guarded_write(self.store, rec, self._store_adapter, self.audit):
                self.stats["rejected_or_failed"] += 1
                return False                                                  # not written -> not registered
            self.registry.add_borrower(borrower_id)
            self.registry.add_conversation(borrower_id, conversation_id)
            self.registry.register_write(borrower_id, self.store, rid, masked_text=f"{masked_text or ''} {reply_masked or ''}", conversation_id=conversation_id, purpose=purpose, consent_id=consent_id)
            self._written.add(rid)
            self.stats["written"] += 1
            self._since_save += 1
            if self.registry_path and self._since_save >= self.save_every:
                self.registry.save(self.registry_path); self._since_save = 0
            return True

    def flush(self):
        """Save the registry now (call before an erasure request if save_every > 1)."""
        with self._lock:
            if self.registry_path:
                self.registry.save(self.registry_path); self._since_save = 0
