"""DynamoDB access. Single table:

  EVENT#<eid> / META            title, starts_at, ends_at, capacity, hold_seconds, seated, next_pos
  EVENT#<eid> / ATT#<pos:06d>   email, name, status, hold_id
  HOLD#<hid>  / META            event_id, att_sk, email, name, status, task_token, sequence

Attendee status: WAITING -> INVITED -> CONFIRMED | DECLINED | EXPIRED
`seated` counts INVITED + CONFIRMED and is only changed inside transactions
guarded by `seated < capacity`, so the event can never be over-booked.
"""
from __future__ import annotations

import os
import time
import uuid

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

TABLE = os.environ.get("TABLE_NAME", "standby")
_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(TABLE)
_client = boto3.client("dynamodb")


def now() -> int:
    return int(time.time())


def ek(eid: str) -> str:
    return f"EVENT#{eid}"


def hk(hid: str) -> str:
    return f"HOLD#{hid}"


def create_event(*, title, starts_at, ends_at, capacity, hold_seconds, location="", description=""):
    eid = uuid.uuid4().hex[:10]
    _table.put_item(
        Item={
            "pk": ek(eid), "sk": "META", "event_id": eid, "title": title,
            "starts_at": starts_at, "ends_at": ends_at, "location": location,
            "description": description, "capacity": int(capacity),
            "hold_seconds": int(hold_seconds), "seated": 0, "next_pos": 0,
            "created_at": now(),
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
    res = _table.update_item(
        Key={"pk": ek(eid), "sk": "META"},
        UpdateExpression="ADD next_pos :one",
        ConditionExpression="attribute_exists(pk)",
        ExpressionAttributeValues={":one": 1},
        ReturnValues="UPDATED_NEW",
    )
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
    Returns the hold item, or None if no seat or nobody waiting."""
    for _ in range(5):
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
            if e.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
            if reasons and reasons[0] == "ConditionalCheckFailed":
                return None  # event is full
            # someone else grabbed this attendee — try the next one
    return None


def get_hold(hid: str) -> dict | None:
    return _table.get_item(Key={"pk": hk(hid), "sk": "META"}).get("Item")


def set_hold_token(hid: str, token: str) -> None:
    _table.update_item(
        Key={"pk": hk(hid), "sk": "META"},
        UpdateExpression="SET task_token = :t, #s = :inv, invited_at = :n",
        ConditionExpression="#s = :p",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":t": token, ":inv": "INVITED", ":p": "PENDING", ":n": now()},
    )


def confirm(hid: str) -> bool:
    h = get_hold(hid)
    if not h:
        return False
    try:
        _client.transact_write_items(
            TransactItems=[
                {"Update": {
                    "TableName": TABLE, "Key": {"pk": {"S": hk(hid)}, "sk": {"S": "META"}},
                    "UpdateExpression": "SET #s = :c REMOVE task_token",
                    "ConditionExpression": "#s = :i",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {":c": {"S": "CONFIRMED"}, ":i": {"S": "INVITED"}},
                }},
                {"Update": {
                    "TableName": TABLE,
                    "Key": {"pk": {"S": ek(h["event_id"])}, "sk": {"S": h["att_sk"]}},
                    "UpdateExpression": "SET #s = :c, confirmed_at = :t",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {":c": {"S": "CONFIRMED"}, ":t": {"N": str(now())}},
                }},
            ]
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "TransactionCanceledException":
            return False
        raise


def release(hid: str, reason: str) -> dict | None:
    """Free the seat held by `hid`. reason: DECLINED | EXPIRED. Idempotent."""
    h = get_hold(hid)
    if not h or h["status"] in ("DECLINED", "EXPIRED"):
        return None
    try:
        _client.transact_write_items(
            TransactItems=[
                {"Update": {
                    "TableName": TABLE, "Key": {"pk": {"S": hk(hid)}, "sk": {"S": "META"}},
                    "UpdateExpression": "SET #s = :r, #seq = #seq + :one, released_at = :t REMOVE task_token",
                    "ConditionExpression": "#s IN (:i, :c, :p)",
                    "ExpressionAttributeNames": {"#s": "status", "#seq": "sequence"},
                    "ExpressionAttributeValues": {
                        ":r": {"S": reason}, ":i": {"S": "INVITED"}, ":c": {"S": "CONFIRMED"},
                        ":p": {"S": "PENDING"}, ":one": {"N": "1"}, ":t": {"N": str(now())},
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
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "TransactionCanceledException":
            return None
        raise
    return get_hold(hid)
