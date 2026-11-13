# Results

Every number in the write-up comes from a run recorded here. All runs: AWS us-east-1,
Lambda python3.13 arm64 256 MB, DynamoDB on-demand, Step Functions Standard, Nov 13 2026.
The account's Lambda concurrency limit was 10 during these runs.

## 1. Check-then-act vs a condition in the write (`seating_compare.jsonl`)

`scripts/compare_seating.py` — 50 people sign up for 30 seats, 10 at a time, against the real
table (no Step Functions, no email). Same table, same attendee code; only the seat-taking differs.

| | Seats given in 10 trials | Over capacity | Seat left empty while people waited | Counter disagrees with reality |
|---|---|---|---|---|
| naive: read `seated`, check in code, write | 30, 29, 31, 30, 31, 29, 30, 31, 28, 31 | 4 / 10 | 3 / 10 | 7 / 10 |
| Standby: `seated < capacity` inside the transaction | 30 × 10 | 0 / 10 | 0 / 10 | 0 / 10 |

The naive version fails in both directions: two writers read the same count, so it oversells,
and a lost update can also leave a free seat nobody is offered.

## 2. Burst through the deployed API (`scripts/burst.py`)

50 simulated people (example.com, never emailed), 30 seats, 60 s holds, 10 concurrent clients
retrying on 429/503.

| Run | Sign-ups took | Throttled retries | Seats held | Waiting |
|---|---|---|---|---|
| 1 | 5.6 s | 2 | 30 | 20 |
| 2 | 4.7 s | 1 | 30 | 20 |
| 3 | 4.7 s | 1 | 30 | 20 |

Then nobody answered: in each event the 30 holds expired together, 20 people were promoted, and
those expired too. End state for all three events: 50 / 50 EXPIRED, 0 seats held,
0 Step Functions executions running or failed (150 executions in total).

## 3. Real calendars

Google Calendar on a Gmail account and a Google Workspace account. Times are from Lambda and
Step Functions logs and start when the reply reached SES (the calendar's own mail delay is not
included).

| What the attendee did | What happened | Reply received → done |
|---|---|---|
| Tapped Yes | Seat confirmed | ~1 s |
| Tapped No | Seat released, event removed from their calendar, invite sent to the next person | ~2 s to the next invite |
| Didn't answer (10 min hold) | Hold expired at the deadline, event removed from their calendar, seat freed | at the deadline |

## 4. Roster deadline and asking again

Venues often need the visitor list (or printed badges) one to two days ahead, and an organizer
knows from experience that a seat offered on the day is a seat nobody takes. So each event has
three times: **ask confirmed people again**, **their answer deadline**, and **the roster deadline**.

Real calendars, compressed to minutes (Nov 14, 05:10–05:21 JST), 1 seat, Gmail first, Workspace second:

| Time | What happened |
|---|---|
| 05:10 | Gmail offered the seat, tapped Yes → confirmed |
| 05:14 | Gmail asked again ("Still coming?"). Google Calendar reset the RSVP to "awaiting reply" |
| 05:17 | No answer by the deadline → seat released, event removed from the Gmail calendar, offered to Workspace with a hold cut short to end before the roster deadline |
| 05:19 | Workspace tapped Yes → confirmed |
| 05:21 | Roster locked. `GET /events/{id}/roster` lists only the Workspace attendee |

`scripts/deadline_check.py` (simulated people, 1 seat, deadline 75 s out): the first hold ended
just before the deadline, the seat was **not** offered to the second person, the roster locked,
and a later sign-up only joined the waitlist. PASS.

## Things the runs found

| Found by | Problem | Fix |
|---|---|---|
| first deploy | `capacity` is a DynamoDB reserved word | attribute-name placeholders |
| burst | two writes to the event item at once → `TransactionConflict` | retry conflicts with jittered backoff; only a failed *condition* means "full" |
| burst | 50 simultaneous calls vs a concurrency limit of 10 → HTTP 503 | clients back off and retry; workflow tasks retry throttles |
| review | an invite that fails to send would hold a seat forever | `States.ALL` catch → release the seat, offer it to the next person |
| organizer feedback | a seat freed on the day is a seat nobody takes; venues lock the visitor list 1–2 days ahead | roster deadline: ask confirmed people again in time, stop moving seats at the deadline |
