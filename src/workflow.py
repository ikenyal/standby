"""Start Step Functions executions: seat offers (until full or the roster locks) and reconfirmations.

Every function here is safe to call again after a failure: executions are named after the hold
(a repeated start is a no-op), and `settle_release` / `reconcile_event` only act on state they
find in DynamoDB, so a retry finishes whatever the failed attempt left undone.
"""
from __future__ import annotations

import json
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import mailer
import store

# Adaptive client-side retries: StartExecution is throttled per account, and a lost start would
# leave a seat held with no workflow to expire it.
_sfn = boto3.client("stepfunctions", config=Config(retries={"mode": "adaptive", "max_attempts": 8}))
STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
LIFECYCLE_ARN = os.environ.get("LIFECYCLE_ARN", "")
MIN_HOLD = 30  # never offer a seat with less time than this to answer
STALE_AFTER = 120  # a hold this old should have a running workflow (seconds)
RELEASED = ("DECLINED", "EXPIRED", "FAILED")


def offer_window(event: dict) -> int:
    """Seconds a newly offered seat may be held: the hold window, cut off at the roster deadline."""
    if "roster_deadline_ts" not in event:  # events created before roster deadlines existed
        return int(event["hold_seconds"])
    left = int(event["roster_deadline_ts"]) - store.now() - 5
    return min(int(event["hold_seconds"]), left)


def _start(arn: str, name: str, payload: dict) -> None:
    """StartExecution, treating "already started under this name" as success (idempotent)."""
    try:
        _sfn.start_execution(stateMachineArn=arn, name=name, input=json.dumps(payload))
    except ClientError as e:
        if e.response["Error"]["Code"] != "ExecutionAlreadyExists":
            raise


def _start_hold(hold_id: str, eid: str, window: int) -> None:
    _start(STATE_MACHINE_ARN, hold_id,
           {"hold_id": hold_id, "event_id": eid, "mode": "offer", "hold_seconds": window})


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
        # If this start fails for good, the hold stays PENDING; reconcile_event re-drives it.
        _start_hold(hold["hold_id"], eid, window)
        started.append(hold["hold_id"])


def settle_release(hid: str) -> list[str]:
    """Finish the work that follows a release: offer the seat to the next person, then tell the
    released attendee. Idempotent, so a retried release step (or a retried inbound reply) still
    completes it even though the release transaction itself already happened."""
    hold = store.get_hold(hid)
    if not hold or hold["status"] not in RELEASED:
        return []
    promoted = fill(hold["event_id"])  # the seat matters more than the courtesy email: do it first
    # FAILED: the invite never arrived, so there is nothing to cancel.
    if hold["status"] != "FAILED" and not hold.get("cancel_sent"):
        mailer.send_cancel(store.get_event(hold["event_id"]), hold, hold["status"])
        store.mark_cancel_sent(hid)  # a duplicate CANCEL (same UID/sequence) is harmless
    return promoted


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
            _start(STATE_MACHINE_ARN, f"{a['hold_id']}-again",
                   {"hold_id": a["hold_id"], "event_id": eid, "mode": "reconfirm", "hold_seconds": window})
            started.append(a["hold_id"])
    return started


def start_lifecycle(eid: str) -> None:
    _start(LIFECYCLE_ARN, f"event-{eid}", {"event_id": eid})


# ---------------------------------------------------------------- reconciliation


def _execution_status(name: str) -> str | None:
    """Status of the hold execution with this name, or None if it was never started."""
    arn = STATE_MACHINE_ARN.replace(":stateMachine:", ":execution:") + f":{name}"
    try:
        return _sfn.describe_execution(executionArn=arn)["status"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ExecutionDoesNotExist":
            return None
        raise


def reconcile_event(eid: str) -> dict:
    """Repair holds whose workflow is gone, then offer any free seats.

    - PENDING hold with no execution (the start was lost): start it now, or give the seat back
      if it is too late to offer it.
    - PENDING / INVITED / RECONFIRMING hold whose execution has ended: nothing will ever answer
      it, so release it and offer the seat to the next person.
    """
    out = {"redriven": 0, "released": 0, "promoted": 0}
    event = store.get_event(eid)
    if not event or event.get("roster_locked"):
        return out
    for a in store.list_attendees(eid):
        if a["status"] not in ("INVITED", "RECONFIRMING") or not a.get("hold_id"):
            continue
        hold = store.get_hold(a["hold_id"])
        if not hold or hold["status"] not in ("PENDING", "INVITED", "RECONFIRMING"):
            continue
        since = max(int(hold.get("created_at", 0)), int(hold.get("asked_again_at", 0)))
        if store.now() - since < STALE_AFTER:
            continue  # just created: its workflow may not have started yet
        name = hold["hold_id"] + ("-again" if hold["status"] == "RECONFIRMING" else "")
        status = _execution_status(name)
        if status == "RUNNING":
            continue
        if status is None and hold["status"] == "PENDING" and offer_window(event) >= MIN_HOLD:
            _start_hold(hold["hold_id"], eid, offer_window(event))
            out["redriven"] += 1
            continue
        # The invite went out only if a task token was stored; otherwise there is nothing to cancel.
        reason = "EXPIRED" if hold.get("task_token") else "FAILED"
        if store.release(hold["hold_id"], reason):
            out["released"] += 1
        out["promoted"] += len(settle_release(hold["hold_id"]))
    out["promoted"] += len(fill(eid))
    return out
