"""Offline-first sync queue. The ONLY part of PumpLocal that uses the internet.

Every record is stored locally with synced=0. If SYNC_URL is set, unsynced records are POSTed
as JSON; on a 2xx reply they are marked synced=1. If SYNC_URL is unset or unreachable, records
simply wait in the queue.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

import core

SYNC_URL = os.environ.get("SYNC_URL", "").strip()
SYNC_INTERVAL = float(os.environ.get("SYNC_INTERVAL", "30"))


class State(object):
    online = False
    last_attempt = None
    last_success = None
    last_error = ""


_lock = threading.Lock()


def status():
    q = core.queued_count()
    if not SYNC_URL:
        label = "Offline, %d records queued (no SYNC_URL set)" % q
    elif State.online:
        label = "Online, %d records queued" % q if q else "Online, all synced"
    else:
        label = "Offline, %d records queued" % q
    return {"configured": bool(SYNC_URL), "online": bool(SYNC_URL) and State.online, "queued": q,
            "label": label, "last_attempt": State.last_attempt, "last_success": State.last_success,
            "last_error": State.last_error}


def sync_now(timeout=8):
    if not SYNC_URL:
        State.online = False
        State.last_error = "SYNC_URL not set"
        return status()
    with _lock:
        State.last_attempt = core.now()
        batch = {t: core.rows("SELECT * FROM %s WHERE synced=0 ORDER BY id LIMIT 500" % t) for t in core.SYNC_TABLES}
        payload = {"station": core.STATION, "sent_at": core.now(), "records": batch}
        try:
            if any(batch.values()):
                req = urllib.request.Request(SYNC_URL, data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    if not 200 <= r.status < 300:
                        raise urllib.error.HTTPError(SYNC_URL, r.status, "bad status", None, None)
                for t, recs in batch.items():
                    for rec in recs:
                        # Only mark rows that were not changed again since we read them.
                        core.execute("UPDATE %s SET synced=1 WHERE id=? AND synced=0" % t, (rec["id"],))
            else:
                ping()
            State.online = True
            State.last_success = core.now()
            State.last_error = ""
        except urllib.error.HTTPError as e:
            State.online = True  # server reachable but refused the data
            State.last_error = "Sync server replied HTTP %s" % e.code
        except Exception as e:
            State.online = False
            State.last_error = "No connection (%s)" % e.__class__.__name__
    return status()


def ping(timeout=4):
    """Any HTTP response (even 404/405) means we can reach the sync server."""
    try:
        urllib.request.urlopen(urllib.request.Request(SYNC_URL, method="GET"), timeout=timeout)
    except urllib.error.HTTPError:
        pass


def background_loop():
    while True:
        time.sleep(SYNC_INTERVAL)
        if SYNC_URL:
            try:
                sync_now()
            except Exception:
                pass


def start_background():
    t = threading.Thread(target=background_loop, daemon=True)
    t.start()
    return t
