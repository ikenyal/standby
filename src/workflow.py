"""Start one Step Functions execution per seat hold until the event is full."""
from __future__ import annotations

import json
import os

import boto3

import store

_sfn = boto3.client("stepfunctions")
STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")


def fill(eid: str) -> list[str]:
    started = []
    event = store.get_event(eid)
    if not event:
        return started
    while True:
        hold = store.seat_next(eid)
        if not hold:
            return started
        _sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=hold["hold_id"],
            input=json.dumps({
                "hold_id": hold["hold_id"],
                "event_id": eid,
                "hold_seconds": int(event["hold_seconds"]),
            }),
        )
        started.append(hold["hold_id"])
