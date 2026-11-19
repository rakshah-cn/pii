"""Nonce uniqueness as an enforced invariant (AES-GCM breaks badly if a (key, nonce) pair is ever reused).

nonce (12 bytes) = EPOCH (4 random bytes, new for every process) || COUNTER (8 bytes, big-endian)
The COUNTER comes from the database table key_nonce_state: allocated atomically per DEK inside the SAME transaction that stores the ciphertext, so
 * two concurrent writers can never receive the same counter (UPDATE ... RETURNING takes a row lock; PostgreSQL and SQLite both serialise it);
 * the counter survives a restart (it is stored, not held in memory);
 * a new DEK (DEK rotation) has its own key_nonce_state row and starts at 0: a new nonce sequence;
 * when the counter reaches Settings.key_usage_limit the DEK refuses further encryption (key_usage_limit_reached): rotate it.
Three independent layers stop a repeat: the counter, the random EPOCH, and the UNIQUE(key_id, nonce) database index on stored records.
HONEST LIMIT: if the whole database is restored from an OLD backup, the stored counter goes backwards. A repeat would then need the new process to draw
the same random 4-byte epoch as an earlier process (about 1 in 4 billion per pair) AND hit the same counter. That is a probabilistic defence, not a proof:
after any restore, rotate the DEKs (operation=DEK), which starts a fresh sequence under a new key."""
import os

EPOCH_BYTES, COUNTER_BYTES = 4, 8


class NonceSource:
    def __init__(self, epoch: bytes = None):
        self.epoch = os.urandom(EPOCH_BYTES) if epoch is None else epoch
        if len(self.epoch) != EPOCH_BYTES:
            raise ValueError("bad_epoch")

    def nonce(self, counter: int) -> bytes:
        if counter < 0 or counter >= 1 << (8 * COUNTER_BYTES):
            raise OverflowError("nonce_counter_out_of_range")
        return self.epoch + counter.to_bytes(COUNTER_BYTES, "big")

    @staticmethod
    def counter_of(nonce: bytes) -> int:
        return int.from_bytes(bytes(nonce)[EPOCH_BYTES:], "big")
