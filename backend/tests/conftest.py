"""Test environment: set BEFORE any `app` import so app.config picks it up.
Uses a throwaway SQLite file, a known owner key, and visitor (demo) mode on.
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="radar-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"
os.environ["DEMO_MODE"] = "1"
os.environ["OWNER_KEY"] = "test-owner-key-123"
os.environ["JWT_SECRET"] = "test-jwt-secret-not-for-production"
os.environ.pop("RAILWAY_ENVIRONMENT", None)
os.environ["DEX_MIN_INTERVAL_S"] = "0"       # no request pacing in tests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
