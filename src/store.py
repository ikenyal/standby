"""DynamoDB access. Single table:

  EVENT#<eid> / META            title, starts_at, ends_at, capacity, hold_seconds, seated, next_pos
  EVENT#<eid> / ATT#<pos:06d>   email, name, status, hold_id
  HOLD#<hid>  / META            event_id, att_sk, email, name, status, task_token, sequence, cancel_sent

GSI OpenEvents (sparse, keys only): `open_events` is set on EVENT META items until the roster
locks, so the reconciler can list open events without scanning the table.

Attendee status: WAITING -> INVITED -> CONFIRMED | DECLINED | EXPIRED
`seated` counts INVITED + CONFIRMED and is only changed inside transactions
guarded by `seated < capacity`, so the event can never be over-booked.
"""
from __future__ import annotations

import os
import random
import time
import uuid

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

TABLE = os.environ.get("TABLE_NAME", "standby")
OPEN_INDEX = "OpenEvents"
_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(TABLE)
_client = boto3.client("dynamodb")


def now() -> int:
    return int(time.time())


class Conflict(Exception):
    """Another transaction touched the same item; safe to retry."""


def _reasons(e: ClientError) -> list[str]:
    return [r.get("Code", "None") for r in e.response.get("CancellationReasons", [])]


def _is_conflict(e: ClientError) -> bool:
    code = e.response["Error"]["Code"]
    if code in ("TransactionConflictException", "ProvisionedThroughputExceededException", "ThrottlingException"):
        return True
    return code == "TransactionCanceledException" and "TransactionConflict" in _reasons(e)


def _retry(fn, attempts: int = 12):
    """Run fn, retrying with jittered backoff while DynamoDB reports a write conflict."""
    for i in range(attempts):
        try:
            return fn()
        except ClientError as e:
            if not _is_conflict(e) or i == attempts - 1:
                raise
        except Conflict:
            if i == attempts - 1:
                raise
        time.sleep(min(1.0, 0.02 * 2 ** i) * random.uniform(0.5, 1.5))


def ek(eid: str) -> str:
    return f"EVENT#{eid}"


def hk(hid: str) -> str:
    return f"HOLD#{hid}"


def _ts(iso: str) -> int:
    from datetime import datetime
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def create_event(*, title, starts_at, ends_at, capacity, hold_seconds, reconfirm_at, reconfirm_by,
                 roster_deadline, location="", description=""):
    eid = uuid.uuid4().hex[:10]
    _table.put_item(
        Item={
            "pk": ek(eid), "sk": "META", "event_id": eid, "title": title,
            "starts_at": starts_at, "ends_at": ends_at, "location": location,
            "description": description, "capacity": int(capacity),
            "hold_seconds": int(hold_seconds), "seated": 0, "next_pos": 0,
            # The venue needs the final list by roster_deadline. Confirmed people are asked again at
            # reconfirm_at and must answer by reconfirm_by, which leaves time to offer freed seats.
            "reconfirm_at": reconfirm_at, "reconfirm_by": reconfirm_by, "roster_deadline": roster_deadline,
            "reconfirm_by_ts": _ts(reconfirm_by), "roster_deadline_ts": _ts(roster_deadline),
            "roster_locked": False, "created_at": now(),
            "open_events": "OPEN",  # sparse GSI key; removed when the roster locks
        },
        ConditionExpression="attribute_not_exists(pk)",
    )
    return eid


def get_event(eid: str) -> dict | None:
    return _table.get_item(Key={"pk": ek(eid), "sk": "META"}).get("Item")


def list_attendees(eid: str) -> list[dict]:
    items, kw = [], {"KeyConditionExpression": Key("pk").eq(ek(eid)) & Key("sk").begins_with("ATT#")}
    while True:
        res = _table.query(**kw)
        items += res["Items"]
        if "LastEvaluatedKey" not in res:
            return items
        kw["ExclusiveStartKey"] = res["LastEvaluatedKey"]


def add_attendee(eid: str, email: str, name: str) -> dict:
    res = _retry(lambda: _table.update_item(
        Key={"pk": ek(eid), "sk": "META"},
        UpdateExpression="ADD next_pos :one",
        ConditionExpression="attribute_exists(pk)",
        ExpressionAttributeValues={":one": 1},
        ReturnValues="UPDATED_NEW",
    ))
    pos = int(res["Attributes"]["next_pos"])
    item = {
        "pk": ek(eid), "sk": f"ATT#{pos:06d}", "event_id": eid, "pos": pos,
        "email": email.strip().lower(), "name": name, "status": "WAITING",
        "joined_at": now(),
    }
    _table.put_item(Item=item)
    return item


def first_waiting(eid: str) -> dict | None:
    for a in list_attendees(eid):  # sorted by pos (sk)
        if a["status"] == "WAITING":
            return a
    return None


def seat_next(eid: str) -> dict | None:
    """Atomically move the first WAITING attendee into a new hold.
    Returns the hold item, or None if the event is full or nobody is waiting."""

    def attempt():
        att = first_waiting(eid)
        if not att:
            return None
        hid = uuid.uuid4().hex
        try:
            _client.transact_write_items(
                TransactItems=[
                    {"Update": {
                        "TableName": TABLE,
                        "Key": {"pk": {"S": ek(eid)}, "sk": {"S": "META"}},
                        "UpdateExpression": "SET #seated = #seated + :one",
                        "ConditionExpression": "#seated < #cap",
                        "ExpressionAttributeNames": {"#seated": "seated", "#cap": "capacity"},
                        "ExpressionAttributeValues": {":one": {"N": "1"}},
                    }},
                    {"Update": {
                        "TableName": TABLE,
                        "Key": {"pk": {"S": ek(eid)}, "sk": {"S": att["sk"]}},
                        "UpdateExpression": "SET #s = :inv, hold_id = :h, invited_at = :t",
                        "ConditionExpression": "#s = :wait",
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {
                            ":inv": {"S": "INVITED"}, ":wait": {"S": "WAITING"},
                            ":h": {"S": hid}, ":t": {"N": str(now())},
                        },
                    }},
                    {"Put": {
                        "TableName": TABLE,
                        "Item": {
                            "pk": {"S": hk(hid)}, "sk": {"S": "META"},
                            "hold_id": {"S": hid}, "event_id": {"S": eid},
                            "att_sk": {"S": att["sk"]}, "email": {"S": att["email"]},
                            "name": {"S": att.get("name") or att["email"]},
                            "status": {"S": "PENDING"}, "sequence": {"N": "0"},
                            "created_at": {"N": str(now())},
                        },
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }},
                ]
            )
            return get_hold(hid)
        except ClientError as e:
            if e.response["Error"]["Code"] != "TransactionCanceledException" or _is_conflict(e):
                raise
            reasons = _reasons(e)
            if reasons and reasons[0] == "ConditionalCheckFailed":
                return None  # event is full
            raise Conflict()  # someone else seated this attendee first: look again

    return _retry(attempt, attempts=25)


def get_hold(hid: str) -> dict | None:
    return _table.get_item(Key={"pk": hk(hid), "sk": "META"}).get("Item")


def set_hold_token(hid: str, token: str) -> None:
    # INVITED is accepted too: a retried invite task carries a new token for the same hold.
    _table.update_item(
        Key={"pk": hk(hid), "sk": "META"},
        UpdateExpression="SET task_token = :t, #s = :inv, invited_at = :n",
        ConditionExpression="#s IN (:p, :inv)",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":t": token, ":inv": "INVITED", ":p": "PENDING", ":n": now()},
    )


def begin_reconfirm(hid: str, token: str) -> dict | None:
    """CONFIRMED -> RECONFIRMING: ask a confirmed attendee again (new invite sequence).
    RECONFIRMING is accepted too, so a retried invite task can store its new token."""
    h = get_hold(hid)
    if not h:
        return None
    try:
        _retry(lambda: _client.transact_write_items(
            TransactItems=[
                {"Update": {
                    "TableName": TABLE, "Key": {"pk": {"S": hk(hid)}, "sk": {"S": "META"}},
                    "UpdateExpression": "SET #s = :r, task_token = :t, #seq = #seq + :one, asked_again_at = :n",
                    "ConditionExpression": "#s IN (:c, :r)",
                    "ExpressionAttributeNames": {"#s": "status", "#seq": "sequence"},
                    "ExpressionAttributeValues": {
                        ":r": {"S": "RECONFIRMING"}, ":c": {"S": "CONFIRMED"}, ":t": {"S": token},
                        ":one": {"N": "1"}, ":n": {"N": str(now())},
                    },
                }},
                {"Update": {
                    "TableName": TABLE,
                    "Key": {"pk": {"S": ek(h["event_id"])}, "sk": {"S": h["att_sk"]}},
                    "UpdateExpression": "SET #s = :r, asked_again_at = :n",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {":r": {"S": "RECONFIRMING"}, ":n": {"N": str(now())}},
                }},
            ]
        ))
    except ClientError as e:
        if e.response["Error"]["Code"] == "TransactionCanceledException" and not _is_conflict(e):
            return None  # no longer confirmed
        raise
    return get_hold(hid)


def lock_roster(eid: str) -> None:
    _retry(lambda: _table.update_item(
        Key={"pk": ek(eid), "sk": "META"},
        UpdateExpression="SET roster_locked = :t, locked_at = :n REMOVE open_events",
        ExpressionAttributeValues={":t": True, ":n": now()},
    ))


def confirm(hid: str) -> bool:
    h = get_hold(hid)
    if not h:
        return False
    try:
        _retry(lambda: _client.transact_write_items(
            TransactItems=[
                {"Update": {
                    "TableName": TABLE, "Key": {"pk": {"S": hk(hid)}, "sk": {"S": "META"}},
                    "UpdateExpression": "SET #s = :c REMOVE task_token",
                    "ConditionExpression": "#s IN (:i, :r)",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {
                        ":c": {"S": "CONFIRMED"}, ":i": {"S": "INVITED"}, ":r": {"S": "RECONFIRMING"},
                    },
                }},
                {"Update": {
                    "TableName": TABLE,
                    "Key": {"pk": {"S": ek(h["event_id"])}, "sk": {"S": h["att_sk"]}},
                    "UpdateExpression": "SET #s = :c, confirmed_at = :t",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {":c": {"S": "CONFIRMED"}, ":t": {"N": str(now())}},
                }},
            ]
        ))
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "TransactionCanceledException" and not _is_conflict(e):
            return False  # already confirmed or released
        raise


def release(hid: str, reason: str) -> dict | None:
    """Free the seat held by `hid`. reason: DECLINED | EXPIRED | FAILED. Idempotent."""
    h = get_hold(hid)
    if not h or h["status"] in ("DECLINED", "EXPIRED", "FAILED"):
        return None
    try:
        _retry(lambda: _client.transact_write_items(
            TransactItems=[
                {"Update": {
                    "TableName": TABLE, "Key": {"pk": {"S": hk(hid)}, "sk": {"S": "META"}},
                    "UpdateExpression": "SET #s = :r, #seq = #seq + :one, released_at = :t REMOVE task_token",
                    "ConditionExpression": "#s IN (:i, :c, :p, :rc)",
                    "ExpressionAttributeNames": {"#s": "status", "#seq": "sequence"},
                    "ExpressionAttributeValues": {
                        ":r": {"S": reason}, ":i": {"S": "INVITED"}, ":c": {"S": "CONFIRMED"},
                        ":p": {"S": "PENDING"}, ":rc": {"S": "RECONFIRMING"},
                        ":one": {"N": "1"}, ":t": {"N": str(now())},
                    },
                }},
                {"Update": {
                    "TableName": TABLE,
                    "Key": {"pk": {"S": ek(h["event_id"])}, "sk": {"S": h["att_sk"]}},
                    "UpdateExpression": "SET #s = :r, released_at = :t",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {":r": {"S": reason}, ":t": {"N": str(now())}},
                }},
                {"Update": {
                    "TableName": TABLE, "Key": {"pk": {"S": ek(h["event_id"])}, "sk": {"S": "META"}},
                    "UpdateExpression": "SET #seated = #seated - :one",
                    "ConditionExpression": "#seated > :zero",
                    "ExpressionAttributeNames": {"#seated": "seated"},
                    "ExpressionAttributeValues": {":one": {"N": "1"}, ":zero": {"N": "0"}},
                }},
            ]
        ))
    except ClientError as e:
        if e.response["Error"]["Code"] == "TransactionCanceledException" and not _is_conflict(e):
            return None  # already released
        raise
    return get_hold(hid)


def mark_cancel_sent(hid: str) -> None:
    _table.update_item(
        Key={"pk": hk(hid), "sk": "META"},
        UpdateExpression="SET cancel_sent = :n",
        ExpressionAttributeValues={":n": now()},
    )


def open_event_ids() -> list[str]:
    """Events whose roster has not locked yet (sparse GSI, keys only)."""
    ids, kw = [], {"IndexName": OPEN_INDEX, "KeyConditionExpression": Key("open_events").eq("OPEN")}
    while True:
        res = _table.query(**kw)
        ids += [i["pk"].split("#", 1)[1] for i in res["Items"]]
        if "LastEvaluatedKey" not in res:
            return ids
        kw["ExclusiveStartKey"] = res["LastEvaluatedKey"]
