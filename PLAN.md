# Standby — design memo

> The waitlist that lives in your calendar.

Entry for the AWS Builder Center Weekend Challenge "No Pipeline, No Problem"
(Nov 13 00:00 PT – Nov 16 13:00 PT, 2026).

## Problem

Small meetups fill up instantly, then a good share of seats go empty on the day,
while people on the waitlist never hear back in time. Organizers end up chasing
RSVPs by hand.

## Idea

Attendees never open a website. Every seat is a calendar invite.

- A seat is offered by sending a calendar invite (iMIP `METHOD:REQUEST`).
- The attendee answers with their calendar's own Yes / No buttons; the calendar
  emails an iMIP `METHOD:REPLY` back to the organizer address.
- Yes → the seat is confirmed. No, or no answer before the hold expires → the seat
  is released (`METHOD:CANCEL`) and the next person on the waitlist gets an invite.
- Seats move from one calendar to another on their own.

Reusable pattern: **a calendar RSVP as the human-approval input of a serverless
workflow** (task token + timeout), applicable to interview slots, equipment
booking, limited stock, etc.

## Architecture (all serverless, scales to zero)

```
Organizer CLI/API ──> API Gateway (HTTP) ──> Lambda: events / attendees
                                                │
                                   DynamoDB (event, seats, waitlist, holds)
                                                │
                Step Functions (Standard) — one execution per seat hold
                  1. SendInvite (Lambda, .waitForTaskToken, TimeoutSeconds = hold window)
                  2a. ACCEPTED  → MarkConfirmed
                  2b. DECLINED / States.Timeout → Release (send CANCEL) → PromoteNext
                                                │
Attendee calendar ──(REPLY email)──> SES inbound ──> S3 ──> Lambda: parse iMIP
                                                         └─> SendTaskSuccess / Failure
```

- **SES** (us-east-1): outbound invites and inbound replies on a demo subdomain.
- **S3**: raw inbound mail (short lifecycle); static organizer board.
- **Lambda** (Python): invite builder, iMIP reply parser, release/promote.
- **DynamoDB** (on-demand): conditional writes / transactions so the seat count
  can never exceed capacity and a seat is promoted exactly once.
- **Step Functions** (Standard, because holds can last days; Express caps at 5 min).
- **AWS SAM**: one-command deploy (`sam deploy --guided`). No CI/CD pipeline.

## Key decisions

- UID of each invite = hold id → a reply maps to exactly one hold.
- iMIP lines arrive folded; the parser unfolds before reading `PARTSTAT`.
- Replies that don't match an open hold, or come from an address that isn't the
  invited attendee, are ignored.
- Fallback: each invite also carries a confirm/decline link that resolves the same
  task token, in case a calendar client doesn't send iMIP replies.
- Hold window is configurable per event (demo: ~60 s).

## Privacy / repo hygiene

- No real email addresses or domains in the repo or commit history.
  Real values live in `samconfig.toml` / `.env` (gitignored); docs use `example.com`.
- Commits use the GitHub noreply identity (enforced by a local hook).
- Demo runs in the SES sandbox: invites can only reach verified addresses.
- The live endpoints are not published; judging uses the write-up and video.

## Scope for the weekend

1. Core loop: invite → reply → confirm / release → promote. (must)
2. Capacity race safety + burst script (50 sign-ups for 30 seats). (must)
3. Organizer board showing seats moving in near real time. (should)
4. Rough idle-cost note in the write-up (verify against pricing pages). (nice)
