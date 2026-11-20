"""Imported FIRST by the test modules: points the config and the borrower list at a throw-away folder, so a test never reads or overwrites the real config.local.json / borrowers.local.json."""
import os
import tempfile

_DIR = tempfile.mkdtemp(prefix="voice_test_")
os.environ["VOICE_CONFIG_PATH"] = os.path.join(_DIR, "config.local.json")
os.environ["VOICE_BORROWERS_PATH"] = os.path.join(_DIR, "borrowers.local.json")
DIR = _DIR
