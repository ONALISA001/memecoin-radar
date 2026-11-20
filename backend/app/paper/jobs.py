"""Background jobs for long backtests.

A 7–30 day backtest or side-by-side comparison can take longer than the hosting proxy
allows for one HTTP request (Netlify cuts proxied requests after ~26 s), which made the
Run / Compare buttons fail. Now the request only STARTS the job and returns its id; the
page polls GET /paper/backtest/jobs/{id} until it is done.

In-process: one worker thread (backtests are CPU-bound; one at a time keeps the API
responsive), results kept for JOB_TTL seconds. A restart loses running jobs — the page
then shows the error and the user can run again.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("paper.jobs")
JOB_TTL = 1800
MAX_PER_USER = 2

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backtest")
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _prune(now: float) -> None:
    for jid in [j for j, v in _jobs.items() if now - v["created"] > JOB_TTL and v["status"] != "running"]:
        _jobs.pop(jid, None)


def submit(user_id: int, kind: str, fn, *args) -> dict:
    """Run fn(*args) in the background. fn must open its own DB session."""
    now = time.time()
    with _lock:
        _prune(now)
        busy = sum(1 for v in _jobs.values() if v["user_id"] == user_id and v["status"] in ("queued", "running"))
        if busy >= MAX_PER_USER:
            raise RuntimeError("a backtest is already running — wait for it to finish")
        jid = uuid.uuid4().hex[:16]
        _jobs[jid] = {"id": jid, "user_id": user_id, "kind": kind, "status": "queued", "created": now,
                      "started": None, "finished": None, "result": None, "error": None}

    def work():
        with _lock:
            _jobs[jid].update(status="running", started=time.time())
        try:
            res = fn(*args)
            with _lock:
                _jobs[jid].update(status="done", result=res, finished=time.time())
        except Exception as exc:  # noqa: BLE001  report, never crash the worker
            log.exception("backtest job %s failed", jid)
            with _lock:
                _jobs[jid].update(status="error", error=str(exc)[:300], finished=time.time())

    _pool.submit(work)
    return public(_jobs[jid])


def get(jid: str, user_id: int) -> dict | None:
    with _lock:
        j = _jobs.get(jid)
        return None if j is None or j["user_id"] != user_id else public(j)


def public(j: dict) -> dict:
    now = time.time()
    out = {k: j[k] for k in ("id", "kind", "status", "error")}
    out["elapsed_s"] = round((j["finished"] or now) - (j["started"] or j["created"]), 1)
    if j["status"] == "done":
        out["result"] = j["result"]
    return out
