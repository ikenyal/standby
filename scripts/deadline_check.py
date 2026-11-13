"""Seats stop moving at the roster deadline.

  STANDBY_API=... STANDBY_API_KEY=... python3 scripts/deadline_check.py

1 seat, 2 simulated people (example.com, never emailed), roster deadline 75 s from now.
The first person is offered the seat but cannot hold it past the deadline, and does not answer.
Expected: their hold ends just before the deadline, the seat is NOT offered to the second person
(nobody can plan around a seat with seconds to go), the roster locks, and later sign-ups wait.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

API, KEY = os.environ["STANDBY_API"].rstrip("/"), os.environ["STANDBY_API_KEY"]


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"x-standby-key": KEY, "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def snap(eid):
    d = call("GET", f"/events/{eid}")
    return d["seated"], {a["name"]: a["status"] for a in d["attendees"]}, d["roster_locked"]


now = datetime.now(timezone.utc).replace(microsecond=0)
start = now + timedelta(days=7)
ev = call("POST", "/events", {
    "title": "Deadline check", "capacity": 1, "hold_seconds": 3600,
    "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(),
    "reconfirm_at": (now + timedelta(seconds=10)).isoformat(),
    "reconfirm_by": (now + timedelta(seconds=20)).isoformat(),
    "roster_deadline": (now + timedelta(seconds=75)).isoformat(),
})
eid = ev["event_id"]
call("POST", f"/events/{eid}/attendees", {"attendees": [
    {"email": "first@example.com", "name": "First"}, {"email": "second@example.com", "name": "Second"}]})
print("t+0  ", snap(eid))
time.sleep(85)
print("t+85 ", snap(eid))
call("POST", f"/events/{eid}/attendees", {"email": "late@example.com", "name": "Late"})
time.sleep(3)
seated, people, locked = snap(eid)
print("late ", (seated, people, locked))
ok = locked and seated == 0 and people == {"First": "EXPIRED", "Second": "WAITING", "Late": "WAITING"}
print("PASS" if ok else "FAIL")
