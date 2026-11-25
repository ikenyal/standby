"""Lambda tasks used by the state machines, plus the scheduled reconciler."""
from __future__ import annotations

import json
import logging

from botocore.exceptions import ClientError

import store
import mailer
import workflow

log = logging.getLogger()
log.setLevel(logging.INFO)

# SES errors that mean "try again shortly", not "this invite can never be sent".
_SEND_THROTTLED = ("TooManyRequestsException", "Throttling", "ThrottlingException", "LimitExceededException")


class RetryableSendError(Exception):
    """SES is throttling us. The state machine retries this error by name before giving up,
    so a busy moment never costs anyone their place in line."""


def _send(fn, *args) -> None:
    try:
        fn(*args)
    except ClientError as e:
        if e.response["Error"]["Code"] in _SEND_THROTTLED:
            raise RetryableSendError(e.response["Error"]["Code"]) from e
        raise


def invite(event, _ctx):
    # Invoked with .waitForTaskToken: {"hold_id", "token", "mode": "offer" | "reconfirm"}.
    # Step Functions retries hand us a fresh token, so storing it must accept a hold that an
    # earlier attempt already moved to INVITED / RECONFIRMING.
    hid, token, mode = event["hold_id"], event["token"], event.get("mode", "offer")
    if mode == "reconfirm":
        hold = store.begin_reconfirm(hid, token)
        if not hold:
            raise RuntimeError("hold is no longer confirmed")
        _send(mailer.send_reconfirm, store.get_event(hold["event_id"]), hold)
    else:
        store.set_hold_token(hid, token)
        hold = store.get_hold(hid)
        _send(mailer.send_invite, store.get_event(hold["event_id"]), hold)
    return {"sent": True}


def confirm(event, _ctx):
    return {"confirmed": store.confirm(event["hold_id"])}


def release(event, _ctx):
    hid, reason = event["hold_id"], event.get("reason", "EXPIRED")
    released = store.release(hid, reason) is not None
    # Always settle, even when an earlier attempt already did the release: that attempt may have
    # failed while sending the cancellation or offering the seat to the next person.
    promoted = workflow.settle_release(hid)
    return {"released": released, "promoted": promoted}


def reconfirm_all(event, _ctx):
    return {"asked_again": len(workflow.start_reconfirm(event["event_id"]))}


def lock_roster(event, _ctx):
    store.lock_roster(event["event_id"])
    return {"locked": True}


def reconcile(_event, _ctx):
    """Scheduled: repair holds whose workflow was lost and offer seats nobody was offered."""
    totals = {"events": 0, "redriven": 0, "released": 0, "promoted": 0}
    failed = []
    for eid in store.open_event_ids():
        totals["events"] += 1
        try:
            for k, v in workflow.reconcile_event(eid).items():
                totals[k] += v
        except Exception:  # one broken event must not stop the others
            log.exception("reconcile failed for event %s", eid)
            failed.append(eid)
    # CloudWatch embedded metric format: the log line becomes metrics without extra API calls.
    print(json.dumps({
        "_aws": {"Timestamp": store.now() * 1000, "CloudWatchMetrics": [{
            "Namespace": "Standby", "Dimensions": [[]],
            "Metrics": [{"Name": "HoldsRedriven", "Unit": "Count"},
                        {"Name": "HoldsReleasedByReconciler", "Unit": "Count"},
                        {"Name": "SeatsPromotedByReconciler", "Unit": "Count"}],
        }]},
        "HoldsRedriven": totals["redriven"],
        "HoldsReleasedByReconciler": totals["released"],
        "SeatsPromotedByReconciler": totals["promoted"],
        "OpenEvents": totals["events"],
    }))
    if failed:  # fail the invocation so the Lambda Errors alarm fires
        raise RuntimeError(f"reconcile failed for {len(failed)} event(s): {', '.join(failed)}")
    return totals
