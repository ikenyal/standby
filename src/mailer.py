"""Outbound invites / cancellations via SES."""
from __future__ import annotations

import os
from datetime import datetime

import boto3

from ical import build_calendar, build_invite_email

ORGANIZER_EMAIL = os.environ.get("ORGANIZER_EMAIL", "host@example.com")
ORGANIZER_NAME = os.environ.get("ORGANIZER_NAME", "Standby")
MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "example.com")
_ses = boto3.client("sesv2")


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


def send_invite(event: dict, hold: dict) -> str:
    window = _human(int(event["hold_seconds"]))
    body = (
        f"A seat at \"{event['title']}\" is yours if you want it.\n\n"
        f"Answer Yes or No from your calendar within {window}. "
        "No answer in time and the seat goes to the next person on the waitlist."
    )
    return _send("REQUEST", event, hold, f"Seat offered: {event['title']}", body)


def send_cancel(event: dict, hold: dict, reason: str) -> str:
    why = "You declined" if reason == "DECLINED" else "The hold expired"
    body = f"{why}, so this seat has been passed to the next person. Thanks for letting it go."
    return _send("CANCEL", event, hold, f"Seat released: {event['title']}", body)
