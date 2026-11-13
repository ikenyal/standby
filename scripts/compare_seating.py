"""Why the seat count lives in a DynamoDB condition, measured.

Runs the same sign-up burst twice against the real table, without Step Functions or email:
  naive : read `seated`, check `seated < capacity` in code, then write   (check-then-act)
  safe  : store.seat_next() — the transaction Standby actually uses        (condition in the write)

  AWS_PROFILE=... TABLE_NAME=<table> python3 scripts/compare_seating.py --trials 5

Writes results/seating_compare.jsonl and prints a summary. Test items use the COMPARE# prefix
and are deleted afterwards.
"""
import argparse
import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import store  # noqa: E402  (uses TABLE_NAME)

T = store._table


def make_event(capacity):
    eid = "COMPARE" + uuid.uuid4().hex[:8]
    T.put_item(Item={"pk": store.ek(eid), "sk": "META", "event_id": eid, "title": "compare",
                     "capacity": capacity, "seated": 0, "next_pos": 0, "hold_seconds": 60,
                     "starts_at": "2026-12-01T10:00:00+00:00", "ends_at": "2026-12-01T11:00:00+00:00"})
    return eid


def naive_seat_next(eid):
    """Check-then-act: the version you write first."""
    meta = T.get_item(Key={"pk": store.ek(eid), "sk": "META"}, ConsistentRead=True)["Item"]
    if int(meta["seated"]) >= int(meta["capacity"]):
        return None
    att = store.first_waiting(eid)
    if not att:
        return None
    T.update_item(Key={"pk": store.ek(eid), "sk": "META"},
                  UpdateExpression="SET seated = :n", ExpressionAttributeValues={":n": int(meta["seated"]) + 1})
    T.update_item(Key={"pk": store.ek(eid), "sk": att["sk"]},
                  UpdateExpression="SET #s = :i", ExpressionAttributeNames={"#s": "status"},
                  ExpressionAttributeValues={":i": "INVITED"})
    return att


def sign_up(mode, eid, i):
    store.add_attendee(eid, f"guest{i:03d}@example.com", f"Guest {i:03d}")
    seat = naive_seat_next if mode == "naive" else store.seat_next
    while seat(eid):  # same shape as workflow.fill()
        pass


def cleanup(eid):
    items = T.query(KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": store.ek(eid)})["Items"]
    holds = [a.get("hold_id") for a in store.list_attendees(eid) if a.get("hold_id")]
    with T.batch_writer() as b:
        for it in items:
            b.delete_item(Key={"pk": it["pk"], "sk": it["sk"]})
        for h in holds:
            b.delete_item(Key={"pk": store.hk(h), "sk": "META"})


def run(mode, people, capacity, workers):
    eid = make_event(capacity)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda i: sign_up(mode, eid, i), range(1, people + 1)))
    secs = time.time() - t0
    atts = store.list_attendees(eid)
    meta = store.get_event(eid)
    invited = sum(1 for a in atts if a["status"] == "INVITED")
    cleanup(eid)
    return {"mode": mode, "people": people, "capacity": capacity, "workers": workers,
            "seats_given": invited, "overbooked": max(0, invited - capacity),
            "counter_says": int(meta["seated"]), "counter_drift": int(meta["seated"]) - invited,
            "seconds": round(secs, 2), "at": int(time.time())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", type=int, default=50)
    ap.add_argument("--capacity", type=int, default=30)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--trials", type=int, default=5)
    a = ap.parse_args()
    out = ROOT / "results" / "seating_compare.jsonl"
    out.parent.mkdir(exist_ok=True)
    rows = []
    for _ in range(a.trials):
        for mode in ("naive", "safe"):
            r = run(mode, a.people, a.capacity, a.workers)
            rows.append(r)
            print(json.dumps(r))
            with out.open("a") as f:
                f.write(json.dumps(r) + "\n")
    for mode in ("naive", "safe"):
        rs = [r for r in rows if r["mode"] == mode]
        print(f"{mode:5s}  seats given per trial: {[r['seats_given'] for r in rs]}  "
              f"(capacity {a.capacity})  overbooked trials: {sum(1 for r in rs if r['overbooked'])}/{len(rs)}  "
              f"counter drift: {[r['counter_drift'] for r in rs]}")


if __name__ == "__main__":
    main()
