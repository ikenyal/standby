"""Cascade test with real SES sends: do seats stay empty while people are still waiting?

  STANDBY_API=https://... STANDBY_API_KEY=... python3 scripts/cascade.py \
      --label before --trials 3 --people 30 --capacity 10 --hold 60 --out results/h2-live-before.jsonl

Like burst.py, N people sign up for C seats, but the addresses are SES mailbox simulator
addresses (success+<run>-<n>@simulator.amazonses.com), so every invite and cancellation really
goes through SES. Nobody answers, so every hold expires; the C holds that expire together send a
burst of CANCELs plus new invites, which can exceed the account's maximum send rate. Simulator
mail does not count toward the 24-hour sending quota, but it is limited by the send rate and
billed like normal mail (fractions of a cent per trial).

Per trial, after all holds settle, it records:
  empty_seats_while_waiting  free seats while people are still WAITING and no hold is in flight
                             (the H2 failure: nobody will ever be offered those seats)
  lost_offers                attendees released as FAILED (their invite could not be sent)
  stuck_invited              holds still INVITED at the timeout (a hold with no workflow)
  sfn_failed / sfn_timed_out failed / timed-out hold executions started during the trial
                             (needs boto3 + AWS credentials; null otherwise, fill in afterwards)

"Settled" means no hold is in flight and either nobody is waiting or every seat is taken. If seats
are free while people wait and nothing is in flight, the trial is "stalled"; it keeps watching
for --grace seconds (long enough for the 5-minute reconciler, if deployed) before recording.
"""
import argparse
import json
import math
import os
import time
import urllib.error
import urllib.request
import uuid
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


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sfn_counts(t0: float, t1: float):
    """Failed / timed-out hold executions started in [t0, t1], if boto3 and credentials exist."""
    arn = os.environ.get("STATE_MACHINE_ARN", "")
    if not arn:
        return None, None
    try:
        import boto3
        sfn = boto3.client("stepfunctions", region_name=arn.split(":")[3])  # region from the ARN, not the profile
        out = []
        for status in ("FAILED", "TIMED_OUT"):
            n = 0
            for page in sfn.get_paginator("list_executions").paginate(stateMachineArn=arn, statusFilter=status):
                n += sum(1 for e in page["executions"] if t0 <= e["startDate"].timestamp() <= t1)
            out.append(n)
        return tuple(out)
    except Exception as e:  # never lose a trial's result over the optional count
        print(f"  (Step Functions count unavailable: {type(e).__name__})", flush=True)
        return None, None


def run_trial(a, label: str, trial: int) -> dict:
    run = uuid.uuid4().hex[:6]
    start = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0)
    t0 = time.time()
    eid = call("POST", "/events", {
        "title": f"Cascade {label} #{trial}: {a.people} people, {a.capacity} seats",
        "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=2)).isoformat(),
        "capacity": a.capacity, "hold_seconds": a.hold,
    })["event_id"]
    RETRIES["n"] = 0
    with ThreadPoolExecutor(max_workers=a.concurrency) as pool:
        list(pool.map(lambda i: call("POST", f"/events/{eid}/attendees", {
            "email": f"success+{run}-{i:03d}@simulator.amazonses.com", "name": f"Sim {i:03d}"}),
            range(1, a.people + 1)))
    signup = time.time() - t0
    print(f"[{label} #{trial}] event {eid}: {a.people} sign-ups in {signup:.1f}s, {RETRIES['n']} throttled retries",
          flush=True)

    rounds = math.ceil(a.people / a.capacity)
    deadline = t0 + a.hold * (2 * rounds + 2) + a.grace + 120
    stalled_since, stall_seen, snap = None, False, None
    while time.time() < deadline:
        time.sleep(a.poll)
        snap = call("GET", f"/events/{eid}")
        c = snap["counts"]
        free = snap["capacity"] - snap["seated"]
        in_flight = c["INVITED"] + c["RECONFIRMING"]
        if in_flight == 0 and (c["WAITING"] == 0 or free == 0):
            break  # settled
        if in_flight == 0 and c["WAITING"] > 0 and free > 0:
            stall_seen = True
            stalled_since = stalled_since or time.time()
            if time.time() - stalled_since >= a.grace:
                break  # still stalled after the grace period: nobody will be offered these seats
        else:
            stalled_since = None
    t1 = time.time()
    c = snap["counts"]
    free = snap["capacity"] - snap["seated"]
    in_flight = c["INVITED"] + c["RECONFIRMING"]
    failed, timed_out = sfn_counts(t0, t1)
    rec = {
        "label": label, "trial": trial, "event_id": eid,
        "people": a.people, "capacity": a.capacity, "hold_seconds": a.hold,
        "t_start": iso(t0), "t_end": iso(t1), "duration_s": round(t1 - t0),
        "signup_s": round(signup, 1), "api_throttled_retries": RETRIES["n"],
        "settled": in_flight == 0 and (c["WAITING"] == 0 or free == 0),
        "seated": snap["seated"], "counts": c,
        "empty_seats_while_waiting": free if (c["WAITING"] > 0 and in_flight == 0) else 0,
        "stall_seen": stall_seen, "grace_s": a.grace,
        "lost_offers": c["FAILED"],
        "stuck_invited": c["INVITED"] if time.time() >= deadline else 0,
        "sfn_failed": failed, "sfn_timed_out": timed_out,
    }
    print(f"[{label} #{trial}] seated={snap['seated']}/{a.capacity} counts={c} "
          f"empty_seats_while_waiting={rec['empty_seats_while_waiting']} lost_offers={rec['lost_offers']}",
          flush=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="e.g. before / after")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--first-trial", type=int, default=1, help="number of the first trial (to continue a series)")
    ap.add_argument("--people", type=int, default=30)
    ap.add_argument("--capacity", type=int, default=10)
    ap.add_argument("--hold", type=int, default=60)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--poll", type=float, default=3.0)
    ap.add_argument("--grace", type=int, default=420, help="seconds to watch a stall (reconciler runs every 300)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.hold < 30:
        ap.error("--hold must be >= 30 (the API minimum)")
    for trial in range(a.first_trial, a.first_trial + a.trials):
        rec = run_trial(a, a.label, trial)
        with open(a.out, "a") as f:
            f.write(json.dumps(rec) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
