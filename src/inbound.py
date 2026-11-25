"""SES receipt-rule Lambda action: turn a calendar REPLY into a task-token callback."""
from __future__ import annotations

import json
import logging
import os

import boto3

import mailer
import store
import workflow
from ical import parse_reply_email

log = logging.getLogger()
log.setLevel(logging.INFO)
_s3 = boto3.client("s3")
_sfn = boto3.client("stepfunctions")
BUCKET = os.environ.get("INBOUND_BUCKET", "")
PREFIX = os.environ.get("INBOUND_PREFIX", "inbound/")


def handler(event, _ctx):
    results = []
    for rec in event.get("Records", []):
        ses = rec.get("ses", {})
        receipt = ses.get("receipt", {})
        if receipt.get("spamVerdict", {}).get("status") == "FAIL" or \
           receipt.get("virusVerdict", {}).get("status") == "FAIL":
            results.append("dropped: spam/virus")
            continue
        mid = ses.get("mail", {}).get("messageId")
        raw = _s3.get_object(Bucket=BUCKET, Key=f"{PREFIX}{mid}")["Body"].read()
        results.append(handle_raw(raw))
    log.info(json.dumps({"results": results}))
    return {"results": results}


def handle_raw(raw: bytes) -> str:
    reply, sender = parse_reply_email(raw)
    if not reply:
        return "ignored: not an iMIP REPLY"
    hold = store.get_hold(mailer.hold_id_from_uid(reply.uid))
    if not hold:
        return "ignored: unknown hold"
    if reply.attendee != hold["email"]:
        return "ignored: attendee mismatch"
    status, ps = hold["status"], reply.partstat
    if status in ("INVITED", "RECONFIRMING") and ps in ("ACCEPTED", "DECLINED") and hold.get("task_token"):
        try:
            _sfn.send_task_success(taskToken=hold["task_token"], output=json.dumps({"partstat": ps}))
        except (_sfn.exceptions.TaskTimedOut, _sfn.exceptions.TaskDoesNotExist, _sfn.exceptions.InvalidToken):
            return "ignored: hold already expired"
        return f"callback: {ps}"
    if status == "CONFIRMED" and ps == "DECLINED":
        # Changed their mind after confirming: free the seat right away.
        store.release(hold["hold_id"], "DECLINED")
        workflow.settle_release(hold["hold_id"])
        return "released after confirm"
    if status == "DECLINED" and ps == "DECLINED":
        # Lambda retried this reply after a failure: finish offering the seat (idempotent).
        workflow.settle_release(hold["hold_id"])
        return "settled already-released hold"
    return f"ignored: status={status} partstat={ps}"
