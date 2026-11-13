"""Burst test: N simulated people sign up at the same moment for an event with C seats.

  STANDBY_API=https://... STANDBY_API_KEY=... python3 scripts/burst.py --people 50 --capacity 30

Simulated attendees use reserved example.com addresses and are never emailed.
Expect exactly C seats held and N - C people waiting, no matter the interleaving.
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

API, KEY = os.environ["STANDBY_API"].rstrip("/"), os.environ["STANDBY_API_KEY"]


RETRIES = {"n": 0}


def call(method, path, body=None):
    # Retry throttled calls (429/503) with backoff, like any well-behaved client.
    for attempt in range(8):
        req = urllib.request.Request(
            API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"x-standby-key": KEY, "content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or attempt == 7:
                raise
            RETRIES["n"] += 1
            time.sleep(0.2 * 2 ** attempt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", type=int, default=50)
    ap.add_argument("--capacity", type=int, default=30)
    ap.add_argument("--hold", type=int, default=60)
    ap.add_argument("--concurrency", type=int, default=10)
    a = ap.parse_args()
    start = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0)
    eid = call("POST", "/events", {
        "title": f"Burst test: {a.people} people, {a.capacity} seats",
        "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=2)).isoformat(),
        "capacity": a.capacity, "hold_seconds": a.hold,
    })["event_id"]
    print("event", eid)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.concurrency) as pool:
        list(pool.map(lambda i: call("POST", f"/events/{eid}/attendees",
                                     {"email": f"guest{i:03d}@example.com", "name": f"Guest {i:03d}"}),
                      range(1, a.people + 1)))
    print(f"{a.people} sign-ups in {time.time() - t0:.1f}s ({a.concurrency} at a time, {RETRIES['n']} throttled retries)")
    snap = call("GET", f"/events/{eid}")
    c = snap["counts"]
    print("seated:", snap["seated"], "counts:", c)
    ok = snap["seated"] == min(a.capacity, a.people) == c["INVITED"] + c["CONFIRMED"] \
        and c["WAITING"] == max(0, a.people - a.capacity)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
