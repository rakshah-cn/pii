from ..config.settings import Settings
from .context import HashChainedEvents, InMemoryEvents, PolicyPermissions, VaultContext
from .deletion_service import DeletionService
from .internal_ops import InternalOps
from .key_lifecycle import KeyLifecycle
from .context_service import ContextService
from .rehydration_service import RehydrationService
from .retention_service import RetentionService
from .vault_service import VaultService


class VaultServices:
    """The assembled vault: tokenizer / rehydration / lifecycle / deletion share one context (settings, db, keys, events)."""

    def __init__(self, ctx: VaultContext):
        self.ctx = ctx
        self.tokenizer = VaultService(ctx)
        self.rehydration = RehydrationService(ctx)
        self.lifecycle = KeyLifecycle(ctx)
        self.deletion = DeletionService(ctx, self.lifecycle)
        self.internal = InternalOps(ctx)
        self.retention = RetentionService(ctx)
        self.context = ContextService(ctx, self.tokenizer)


def build_services(settings=None, db=None, permissions=None, events=None, clock=None) -> VaultServices:
    return VaultServices(VaultContext(settings or Settings.from_env(), db=db, permissions=permissions, events=events, clock=clock))
