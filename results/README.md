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

## Things the runs found

| Found by | Problem | Fix |
|---|---|---|
| first deploy | `capacity` is a DynamoDB reserved word | attribute-name placeholders |
| burst | two writes to the event item at once → `TransactionConflict` | retry conflicts with jittered backoff; only a failed *condition* means "full" |
| burst | 50 simultaneous calls vs a concurrency limit of 10 → HTTP 503 | clients back off and retry; workflow tasks retry throttles |
| review | an invite that fails to send would hold a seat forever | `States.ALL` catch → release the seat, offer it to the next person |
