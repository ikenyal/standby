"""Lambda tasks used by the state machines."""
from __future__ import annotations

import store
import mailer
import workflow


def invite(event, _ctx):
    # Invoked with .waitForTaskToken: {"hold_id", "token", "mode": "offer" | "reconfirm"}
    hid, token, mode = event["hold_id"], event["token"], event.get("mode", "offer")
    if mode == "reconfirm":
        hold = store.begin_reconfirm(hid, token)
        if not hold:
            raise RuntimeError("hold is no longer confirmed")
        mailer.send_reconfirm(store.get_event(hold["event_id"]), hold)
    else:
        store.set_hold_token(hid, token)
        hold = store.get_hold(hid)
        mailer.send_invite(store.get_event(hold["event_id"]), hold)
    return {"sent": True}


def confirm(event, _ctx):
    return {"confirmed": store.confirm(event["hold_id"])}


def release(event, _ctx):
    hid, reason = event["hold_id"], event.get("reason", "EXPIRED")
    hold = store.release(hid, reason)
    if hold:
        ev = store.get_event(hold["event_id"])
        if reason != "FAILED":  # the invite never arrived, so there is nothing to cancel
            mailer.send_cancel(ev, hold, reason)
        promoted = workflow.fill(hold["event_id"])
    else:
        promoted = []
    return {"released": bool(hold), "promoted": promoted}


def reconfirm_all(event, _ctx):
    return {"asked_again": len(workflow.start_reconfirm(event["event_id"]))}


def lock_roster(event, _ctx):
    store.lock_roster(event["event_id"])
    return {"locked": True}
