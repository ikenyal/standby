"""Outbound invites / cancellations via SES."""
from __future__ import annotations

import os
import time
from datetime import datetime

import boto3
from botocore.config import Config

from ical import build_calendar, build_invite_email

ORGANIZER_EMAIL = os.environ.get("ORGANIZER_EMAIL", "host@example.com")
ORGANIZER_NAME = os.environ.get("ORGANIZER_NAME", "Standby")
MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "example.com")
# Adaptive retries absorb short SES throttling (the sandbox allows ~1 message/s, and a cascade of
# expiring holds sends many cancellations at once). steps.invite retries anything left over.
_ses = boto3.client("sesv2", config=Config(retries={"mode": "adaptive", "max_attempts": 6}))


def uid_for(hold_id: str) -> str:
    return f"{hold_id}@{MAIL_DOMAIN}"


def hold_id_from_uid(uid: str) -> str:
    return uid.split("@", 1)[0]


def _human(seconds: int) -> str:
    if seconds < 120:
        return f"{seconds} seconds"
    if seconds < 7200:
        return f"{seconds // 60} minutes"
    if seconds < 172800:
        return f"{seconds // 3600} hours"
    return f"{seconds // 86400} days"


SIMULATED_DOMAINS = ("example.com", "example.org", "example.net")


def is_simulated(addr: str) -> bool:
    """Reserved example domains are simulated attendees: never emailed (used for load tests)."""
    domain = addr.rsplit("@", 1)[-1].lower()
    return any(domain == d or domain.endswith("." + d) for d in SIMULATED_DOMAINS)


def _send(method: str, event: dict, hold: dict, subject: str, body: str) -> str:
    if is_simulated(hold["email"]):
        return "simulated"
    cal = build_calendar(
        method=method,
        uid=uid_for(hold["hold_id"]),
        sequence=int(hold.get("sequence", 0)),
        title=event["title"],
        description=body,
        starts_at=datetime.fromisoformat(event["starts_at"]),
        ends_at=datetime.fromisoformat(event["ends_at"]),
        location=event.get("location", ""),
        organizer_email=ORGANIZER_EMAIL,
        organizer_name=ORGANIZER_NAME,
        attendee_email=hold["email"],
        attendee_name=hold.get("name") or hold["email"],
    )
    raw = build_invite_email(
        method=method, calendar=cal, subject=subject, body=body,
        from_addr=f"{ORGANIZER_NAME} <{ORGANIZER_EMAIL}>", to_addr=hold["email"],
    )
    res = _ses.send_email(
        FromEmailAddress=ORGANIZER_EMAIL,
        Destination={"ToAddresses": [hold["email"]]},
        Content={"Raw": {"Data": raw}},
    )
    return res["MessageId"]


def _when(event: dict, iso: str) -> str:
    tz = datetime.fromisoformat(event["starts_at"]).tzinfo
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz).strftime("%a %d %b, %H:%M")


def send_reconfirm(event: dict, hold: dict) -> str:
    body = (
        f"Still coming to \"{event['title']}\"? The final list goes to the venue on "
        f"{_when(event, event['roster_deadline'])}.\n\n"
        f"Answer Yes or No again from your calendar by {_when(event, event['reconfirm_by'])}. "
        "If we don't hear back, your seat goes to the next person while they still have time to plan."
    )
    return _send("REQUEST", event, hold, f"Still coming? {event['title']}", body)


def send_invite(event: dict, hold: dict) -> str:
    limit = int(event.get("roster_deadline_ts", time.time() + int(event["hold_seconds"]))) - int(time.time())
    window = _human(max(1, min(int(event["hold_seconds"]), limit)))
    body = (
        f"A seat at \"{event['title']}\" is yours if you want it.\n\n"
        f"Answer Yes or No from your calendar within {window}. "
        "No answer in time and the seat goes to the next person on the waitlist."
    )
    return _send("REQUEST", event, hold, f"Seat offered: {event['title']}", body)


def send_cancel(event: dict, hold: dict, reason: str) -> str:
    if reason == "DECLINED":
        body = "You declined, so this seat has been released. Thanks for letting us know."
    else:
        body = "We didn't hear back in time, so this seat has been released."
    return _send("CANCEL", event, hold, f"Seat released: {event['title']}", body)
