"""Runs every backend/scripts/test_*.py suite under pytest, so CI covers them too.

Each script sets its own throwaway database and settings at import time and exits
non-zero when any check fails, so each one runs in its own fresh Python process.
None of them needs the network (all external APIs are faked inside the scripts).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((BACKEND / "scripts").glob("test_*.py"))
# set by tests/conftest.py for the in-process tests; the scripts choose their own
CONFTEST_ENV = ("DATABASE_URL", "DEMO_MODE", "OWNER_KEY", "JWT_SECRET")


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in CONFTEST_ENV}
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def test_scripts_found():
    assert len(SCRIPTS) >= 20, f"expected the script suites, found {len(SCRIPTS)}"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.stem)
def test_script_suite(script: Path):
    proc = subprocess.run([sys.executable, str(script)], cwd=BACKEND, env=_env(),
                          capture_output=True, text=True, timeout=300)
    out = proc.stdout + proc.stderr
    failed = [line.strip() for line in out.splitlines() if line.lstrip().startswith("❌")]
    assert proc.returncode == 0, (
        f"{script.name} failed (exit {proc.returncode}).\n"
        + ("Failed checks:\n  " + "\n  ".join(failed[:20]) + "\n" if failed else "")
        + "Last output:\n" + "\n".join(out.splitlines()[-30:]))
