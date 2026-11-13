"""Start Step Functions executions: seat offers (until full or the roster locks) and reconfirmations."""
from __future__ import annotations

import json
import os

import boto3

import store

_sfn = boto3.client("stepfunctions")
STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
LIFECYCLE_ARN = os.environ.get("LIFECYCLE_ARN", "")
MIN_HOLD = 30  # never offer a seat with less time than this to answer


def offer_window(event: dict) -> int:
    """Seconds a newly offered seat may be held: the hold window, cut off at the roster deadline."""
    if "roster_deadline_ts" not in event:  # events created before roster deadlines existed
        return int(event["hold_seconds"])
    left = int(event["roster_deadline_ts"]) - store.now() - 5
    return min(int(event["hold_seconds"]), left)


def fill(eid: str) -> list[str]:
    """Offer free seats to the waitlist, in order, until the event is full or the roster locks."""
    started = []
    event = store.get_event(eid)
    if not event or event.get("roster_locked"):
        return started
    while True:
        window = offer_window(event)
        if window < MIN_HOLD:
            return started  # too close to the roster deadline: nobody can plan around a seat now
        hold = store.seat_next(eid)
        if not hold:
            return started
        _sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=hold["hold_id"],
            input=json.dumps({"hold_id": hold["hold_id"], "event_id": eid, "mode": "offer",
                              "hold_seconds": window}),
        )
        started.append(hold["hold_id"])


def start_reconfirm(eid: str) -> list[str]:
    """Ask every confirmed attendee again; they must answer by reconfirm_by."""
    event = store.get_event(eid)
    if not event or event.get("roster_locked"):
        return []
    window = int(event["reconfirm_by_ts"]) - store.now()
    if window < MIN_HOLD:
        return []
    started = []
    for a in store.list_attendees(eid):
        if a["status"] == "CONFIRMED" and a.get("hold_id"):
            _sfn.start_execution(
                stateMachineArn=STATE_MACHINE_ARN,
                name=f"{a['hold_id']}-again",
                input=json.dumps({"hold_id": a["hold_id"], "event_id": eid, "mode": "reconfirm",
                                  "hold_seconds": window}),
            )
            started.append(a["hold_id"])
    return started


def start_lifecycle(eid: str) -> None:
    _sfn.start_execution(stateMachineArn=LIFECYCLE_ARN, name=f"event-{eid}",
                         input=json.dumps({"event_id": eid}))
