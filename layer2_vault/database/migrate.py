"""Run schema migrations as the migration role.     VAULT_DB_URL=postgresql://vault_migration@host/db?schema=vault python -m layer2_vault.database.migrate
The running vault should connect as vault_app with VAULT_DB_AUTO_MIGRATE=false and will refuse to start if the schema version is not the one it expects."""
import os
import sys

from ..core.errors import VaultError
from .connection import Database
from .models import SCHEMA_VERSION


def main(url=None):
    url = url or os.environ.get("VAULT_DB_URL")
    if not url:
        print("VAULT_DB_URL is not set"); return 2
    try:
        db = Database(url, migrate=True)
    except VaultError as e:
        print(f"migration failed: {e.code}"); return 1
    print(f"schema now at version {SCHEMA_VERSION} (was {db.migrated_from})")
    db.close(); return 0


if __name__ == "__main__":
    sys.exit(main())
