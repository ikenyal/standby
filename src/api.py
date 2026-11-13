"""Organizer API (HTTP API). Every call needs the x-standby-key header."""
from __future__ import annotations

import hmac
import json
import os
from datetime import datetime

import store
import workflow

API_KEY = os.environ.get("API_KEY", "")
PUBLIC_STATUSES = ("WAITING", "INVITED", "CONFIRMED", "DECLINED", "EXPIRED")


def _resp(code: int, body) -> dict:
    return {
        "statusCode": code,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body, default=lambda o: int(o) if hasattr(o, "to_integral_value") else str(o)),
    }


def _mask(email: str) -> str:
    # The board never shows full addresses.
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain[:1]}***"


def _snapshot(eid: str) -> dict | None:
    ev = store.get_event(eid)
    if not ev:
        return None
    atts = store.list_attendees(eid)
    return {
        "event_id": eid,
        "title": ev["title"],
        "starts_at": ev["starts_at"],
        "capacity": int(ev["capacity"]),
        "seated": int(ev["seated"]),
        "hold_seconds": int(ev["hold_seconds"]),
        "attendees": [
            {
                "pos": int(a["pos"]),
                "name": a.get("name") or "",
                "email": _mask(a["email"]),
                "status": a["status"],
                "invited_at": int(a["invited_at"]) if a.get("invited_at") else None,
            }
            for a in atts
        ],
        "counts": {s: sum(1 for a in atts if a["status"] == s) for s in PUBLIC_STATUSES},
        "server_time": store.now(),
    }


def handler(event, _ctx):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not API_KEY or not hmac.compare_digest(headers.get("x-standby-key", ""), API_KEY):
        return _resp(401, {"error": "unauthorized"})
    route = event.get("routeKey", "")
    params = event.get("pathParameters") or {}
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid json"})

    if route == "POST /events":
        try:
            datetime.fromisoformat(body["starts_at"]); datetime.fromisoformat(body["ends_at"])
            capacity, hold = int(body["capacity"]), int(body.get("hold_seconds", 60))
        except (KeyError, ValueError):
            return _resp(400, {"error": "title, starts_at, ends_at (ISO 8601 with offset), capacity required"})
        if capacity < 1 or hold < 30:
            return _resp(400, {"error": "capacity >= 1 and hold_seconds >= 30"})
        eid = store.create_event(
            title=body.get("title", "Untitled meetup"), starts_at=body["starts_at"],
            ends_at=body["ends_at"], capacity=capacity, hold_seconds=hold,
            location=body.get("location", ""),
        )
        return _resp(201, {"event_id": eid})

    eid = params.get("id", "")
    if route == "GET /events/{id}":
        snap = _snapshot(eid)
        return _resp(200, snap) if snap else _resp(404, {"error": "not found"})

    if route == "POST /events/{id}/attendees":
        if not store.get_event(eid):
            return _resp(404, {"error": "not found"})
        people = body.get("attendees") or [body]
        added = []
        for p in people:
            if "@" not in p.get("email", ""):
                return _resp(400, {"error": "email required"})
            added.append(store.add_attendee(eid, p["email"], p.get("name", ""))["pos"])
        started = workflow.fill(eid)
        return _resp(201, {"added": added, "holds_started": len(started)})

    return _resp(404, {"error": "no route"})
