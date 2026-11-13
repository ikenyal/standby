"""Lambda tasks used by the hold state machine."""
from __future__ import annotations

import mailer
import store
import workflow


def invite(event, _ctx):
    # Invoked with .waitForTaskToken: {"hold_id": ..., "token": ...}
    hid, token = event["hold_id"], event["token"]
    store.set_hold_token(hid, token)
    hold = store.get_hold(hid)
    ev = store.get_event(hold["event_id"])
    mailer.send_invite(ev, hold)
    return {"sent": True}


def confirm(event, _ctx):
    return {"confirmed": store.confirm(event["hold_id"])}


def release(event, _ctx):
    hid, reason = event["hold_id"], event.get("reason", "EXPIRED")
    hold = store.release(hid, reason)
    if hold:
        ev = store.get_event(hold["event_id"])
        mailer.send_cancel(ev, hold, reason)
        promoted = workflow.fill(hold["event_id"])
    else:
        promoted = []
    return {"released": bool(hold), "promoted": promoted}
