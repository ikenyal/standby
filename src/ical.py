"""Minimal iCalendar / iMIP helpers: build REQUEST/CANCEL invites, parse REPLY."""
from __future__ import annotations

import email
import email.policy
import email.utils
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    # RFC 5545: lines longer than 75 octets are folded with CRLF + space.
    out, raw = [], line.encode("utf-8")
    while len(raw) > 75:
        cut = 75
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:  # don't split a UTF-8 sequence
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n ".join(out)


def build_calendar(
    *,
    method: str,
    uid: str,
    sequence: int,
    title: str,
    description: str,
    starts_at: datetime,
    ends_at: datetime,
    organizer_email: str,
    organizer_name: str,
    attendee_email: str,
    attendee_name: str,
    location: str = "",
) -> str:
    method = method.upper()
    cancelled = method == "CANCEL"
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//ikenyal//Standby//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{_fmt(datetime.now(timezone.utc))}",
        f"DTSTART:{_fmt(starts_at)}",
        f"DTEND:{_fmt(ends_at)}",
        f"SUMMARY:{_escape(title)}",
        f"DESCRIPTION:{_escape(description)}",
    ]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    lines += [
        f"ORGANIZER;CN={_escape(organizer_name)}:mailto:{organizer_email}",
        "ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;"
        f"PARTSTAT=NEEDS-ACTION;RSVP={'FALSE' if cancelled else 'TRUE'};"
        f"CN={_escape(attendee_name)}:mailto:{attendee_email}",
        f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}",
        "TRANSP:OPAQUE",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def build_invite_email(
    *, method: str, calendar: str, subject: str, body: str, from_addr: str, to_addr: str
) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)
    msg.add_alternative(calendar, subtype="calendar", params={"method": method.upper()})
    # Some clients only look at an attached .ics; add one as well.
    msg.add_attachment(
        calendar.encode("utf-8"),
        maintype="application",
        subtype="ics",
        filename="invite.ics",
    )
    return msg.as_bytes()


# ---------------------------------------------------------------- parsing


@dataclass(frozen=True)
class Reply:
    uid: str
    attendee: str  # lower-cased email
    partstat: str  # ACCEPTED / DECLINED / TENTATIVE / ...
    sequence: int


def unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return [l for l in lines if l]


def _split_prop(line: str) -> tuple[str, dict[str, str], str]:
    # NAME;PARAM=V;PARAM="V:x":value  — colons may appear inside quoted params.
    in_q, idx = False, -1
    for i, ch in enumerate(line):
        if ch == '"':
            in_q = not in_q
        elif ch == ":" and not in_q:
            idx = i
            break
    head, value = (line, "") if idx < 0 else (line[:idx], line[idx + 1 :])
    parts = head.split(";")
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, value


def parse_reply_calendar(text: str) -> Reply | None:
    method, uid, seq, attendee, partstat, in_event = "", "", 0, "", "", False
    for line in unfold(text):
        name, params, value = _split_prop(line)
        if name == "METHOD":
            method = value.strip().upper()
        elif name == "BEGIN" and value.upper() == "VEVENT":
            in_event = True
        elif name == "END" and value.upper() == "VEVENT":
            in_event = False
        elif in_event and name == "UID":
            uid = value.strip()
        elif in_event and name == "SEQUENCE":
            try:
                seq = int(value.strip())
            except ValueError:
                pass
        elif in_event and name == "ATTENDEE" and not attendee:
            addr = value.strip()
            if addr.lower().startswith("mailto:"):
                addr = addr[7:]
            attendee = addr.lower()
            partstat = params.get("PARTSTAT", "").upper()
    if method != "REPLY" or not uid or not attendee or not partstat:
        return None
    return Reply(uid=uid, attendee=attendee, partstat=partstat, sequence=seq)


def parse_reply_email(raw: bytes) -> tuple[Reply | None, str]:
    """Return (reply, envelope_from) from a raw RFC 822 message."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = email.utils.parseaddr(str(msg.get("From", "")))[1].lower()
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype in ("text/calendar", "application/ics"):
            payload = part.get_payload(decode=True) or b""
            reply = parse_reply_calendar(payload.decode(part.get_content_charset() or "utf-8", "replace"))
            if reply:
                return reply, sender
    return None, sender
