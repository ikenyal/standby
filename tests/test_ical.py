"""Run: python3 tests/test_ical.py  (no AWS needed)."""
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ical import build_calendar, build_invite_email, parse_reply_calendar, parse_reply_email, unfold  # noqa: E402

# A REPLY shaped like the ones calendar clients send: long ATTENDEE line folded mid-parameter.
REPLY = (
    "BEGIN:VCALENDAR\r\nPRODID:-//Example Calendar//EN\r\nVERSION:2.0\r\nMETHOD:REPLY\r\n"
    "BEGIN:VEVENT\r\nDTSTART:20261120T100000Z\r\nUID:abc123@standby.example.com\r\nSEQUENCE:0\r\n"
    "ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;CN=Ada Lo\r\n"
    " velace;X-NUM-GUESTS=0:mailto:Ada@Example.com\r\n"
    "ORGANIZER;CN=\"Standby: host\":mailto:host@standby.example.com\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)


def test_unfold():
    lines = unfold(REPLY)
    assert any(l.endswith("mailto:Ada@Example.com") and "Lovelace" in l for l in lines)


def test_parse_reply():
    r = parse_reply_calendar(REPLY)
    assert r and r.uid == "abc123@standby.example.com"
    assert r.attendee == "ada@example.com" and r.partstat == "ACCEPTED" and r.sequence == 0


def test_not_a_reply():
    assert parse_reply_calendar(REPLY.replace("METHOD:REPLY", "METHOD:REQUEST")) is None


def test_reply_in_email():
    m = EmailMessage()
    m["From"] = "Ada <ada@example.com>"
    m["To"] = "host@standby.example.com"
    m["Subject"] = "Accepted: Meetup"
    m.set_content("Ada has accepted.")
    m.add_alternative(REPLY.replace("PARTSTAT=ACCEPTED", "PARTSTAT=DECLINED"),
                      subtype="calendar", params={"method": "REPLY"})
    r, sender = parse_reply_email(m.as_bytes())
    assert r and r.partstat == "DECLINED" and sender == "ada@example.com"


def test_build_roundtrip():
    cal = build_calendar(
        method="REQUEST", uid="h1@standby.example.com", sequence=0, title="Serverless night; vol.1",
        description="Line one\nLine two, with comma", location="Online",
        starts_at=datetime(2026, 11, 20, 10, tzinfo=timezone.utc),
        ends_at=datetime(2026, 11, 20, 11, tzinfo=timezone.utc),
        organizer_email="host@standby.example.com", organizer_name="Standby",
        attendee_email="ada@example.com", attendee_name="Ada Lovelace " * 6,
    )
    assert all(len(l.encode()) <= 76 for l in cal.split("\r\n"))  # incl. leading fold space
    flat = "\r\n".join(unfold(cal))
    assert "METHOD:REQUEST" in flat and "RSVP=TRUE" in flat
    # A client echoing our invite back as a REPLY must map to the same UID.
    r = parse_reply_calendar(flat.replace("METHOD:REQUEST", "METHOD:REPLY").replace("NEEDS-ACTION", "ACCEPTED"))
    assert r and r.uid == "h1@standby.example.com" and r.attendee == "ada@example.com"
    raw = build_invite_email(method="REQUEST", calendar=cal, subject="s", body="b",
                             from_addr="Standby <host@standby.example.com>", to_addr="ada@example.com")
    assert b"text/calendar" in raw and b"method=\"REQUEST\"" in raw


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name)
