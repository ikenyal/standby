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

## 5. Tap to seat, end to end

The reply's own `Date` header (written by the attendee's mail server when they tap) compared with
the time the seat changed in DynamoDB. One-second resolution on both sides.

| Reply | Tapped (reply `Date`) | Reached SES | Seat changed | Tap → done |
|---|---|---|---|---|
| Gmail, Yes | 18:02:04 | 18:02:04 | confirmed 18:02:07 | 3 s |
| Gmail, No | 18:06:02 | 18:06:03 | released, next person invited 18:06:05 | 3 s |
| Gmail, Yes | 05:12:13 | 05:12:13 | confirmed 05:12:16 | 3 s |
| Workspace, Yes | 05:19:14 | 05:19:15 | confirmed 05:19:17 | 3 s |

Calendar to SES took 0–1 s in all five replies received.

## 6. What it costs

`scripts/cost_model.py` multiplies measured usage by us-east-1 list prices (checked Nov 14 2026,
free tiers ignored): Step Functions transitions per hold path from execution histories (answered: 3,
expired: 2, per event: 5), Lambda average billed duration from CloudWatch (88–786 ms at 256 MB),
one email per invite / ask / cancellation, one inbound email per reply.

An event with 80 sign-ups, 50 seats, and 10 people who stay silent when asked again:

| Service | USD |
|---|---|
| Step Functions | 0.0081 |
| SES out (invites, asks, cancellations) | 0.0120 |
| SES in (replies) | 0.0100 |
| Lambda | 0.0007 |
| API Gateway | 0.0001 |
| DynamoDB (rough upper bound) | 0.0016 |
| S3 (raw replies) | 0.0005 |
| **Total** | **≈ 0.033** |

Leaving the board open for an hour (polling every 2 s) adds about $0.003. Nothing in the stack is
billed by the hour; between events the only cost is storage of a few kilobytes in DynamoDB and
S3 (raw replies expire after 7 days).

Prices: [Step Functions](https://aws.amazon.com/step-functions/pricing/),
[SES](https://aws.amazon.com/ses/pricing/), [Lambda](https://aws.amazon.com/lambda/pricing/),
[API Gateway](https://aws.amazon.com/api-gateway/pricing/),
[DynamoDB on-demand](https://aws.amazon.com/dynamodb/pricing/on-demand/),
[S3](https://aws.amazon.com/s3/pricing/).

## 7. Live cascade with real SES sends (H2 before/after)

`scripts/cascade.py` against the deployed stack, Nov 25 2026. The stack was first confirmed to run
`d7095ba` byte for byte: building `src/` at that commit gives the SAM artifact checksum the
deployed functions were uploaded under, and the deployed template is semantically identical.
30 people, 10 seats, 60 s holds, nobody answers. Attendees are SES mailbox simulator addresses,
so every invite and cancellation really goes through SES (simulator mail does not count toward
the 24-hour quota: `SentLast24Hours` stayed at 0, but it is limited by the send rate).

**Natural cascade (`h2-live-before.jsonl`): did not reproduce H2.** 3 of 3 trials ended with 0
seats empty while people waited, 0 lost offers, 0 failed or timed-out executions. The H2 failure
needs an SES error to reach the release step, i.e. throttling that outlasts the SDK's own retries.
That never happened: the account's **Lambda concurrency limit of 10** throttles the invite and
release functions themselves (10 and 1 Lambda throttles, retried by Step Functions), which spreads
the sends out. SES peaked at 20 sends per minute against a limit of 1 per second, the Invite and
Release functions had 0 errors, and the longest release took 1.1 s.

**Controlled fault injection (`h2-fault-before.jsonl`): reproduced H2 in 3 of 3 trials.** Same
cascade, with one fault: a temporary inline Deny on `ses:SendEmail` / `ses:SendRawEmail` on the
Release function's role only (`scripts/fault/deny-ses-release.json`, applied and removed by
`scripts/fault_cascade.sh` with an exit trap; removal verified afterwards). No application code was
changed, so the same run applies to the fixed stack.

| Trial | Event | Seats empty while people wait | Waiting | Hold executions |
|---|---|---|---|---|
| 1 | 5b60c70cc8 | **10 / 10** | 20 | 10 SUCCEEDED, 0 failed, 0 timed out |
| 2 | 1b1aa7b9f6 | **10 / 10** | 20 | 10 SUCCEEDED, 0 failed, 0 timed out |
| 3 | be684fd0df | **10 / 10** | 20 | 10 SUCCEEDED, 0 failed, 0 timed out |

The seats stayed empty for the whole 7-minute watch. The Release function ran 60 times for 30
expired holds with 30 errors (AccessDenied): each first attempt freed the seat and then failed on
the CANCEL; each retry found the hold already released and returned without offering the seat.
Every execution still ended SUCCEEDED, so an alarm on failed executions would not have fired.

## Things the runs found

| Found by | Problem | Fix |
|---|---|---|
| first deploy | `capacity` is a DynamoDB reserved word | attribute-name placeholders |
| burst | two writes to the event item at once → `TransactionConflict` | retry conflicts with jittered backoff; only a failed *condition* means "full" |
| burst | 50 simultaneous calls vs a concurrency limit of 10 → HTTP 503 | clients back off and retry |
| execution history, afterwards | 2 people lost their seat because the invite Lambda was throttled (429) and the throttle was treated as "could not send" | retry throttling on the waiting task before giving up; 0 such losses in the 150 holds after the fix |
| review | an invite that fails to send would hold a seat forever | `States.ALL` catch → release the seat, offer it to the next person |
| review (Well-Architected) | a release that failed after freeing the seat (e.g. SES throttled the CANCEL) was retried as "already released", so the next person was never invited; a lost `StartExecution` held a seat forever | follow-ups run on every retry; a 5-minute reconciler repairs orphaned holds. Tests: `tests/test_seat_recovery.py` ([before](h2-tests-before.txt), [after](h2-tests-after.txt)) |
| security review of that fix | the reconciler could release a hold that was confirmed between its read and its workflow check | release is conditional on the exact status the reconciler saw ([race before](h2-race-before.txt)) |
| organizer feedback | a seat freed on the day is a seat nobody takes; venues lock the visitor list 1–2 days ahead | roster deadline: ask confirmed people again in time, stop moving seats at the deadline |
