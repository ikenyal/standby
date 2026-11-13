# Pattern: a calendar RSVP as a Step Functions callback

Wait for a human's yes/no **inside a serverless workflow**, where the human answers with the
Yes / No buttons their calendar already has. No app, no login, no link to click.

```
Step Functions task (.waitForTaskToken, TimeoutSeconds = how long they get)
   └─ Lambda: store token under a key, email a calendar invite whose UID is that key
                                   … the person taps Yes or No in their calendar …
SES inbound (your subdomain's MX) ─► S3 (raw mail) ─► Lambda: parse the iMIP REPLY
   └─ UID → key → token ─► SendTaskSuccess {"partstat": "ACCEPTED" | "DECLINED"}
No answer in time ─► States.Timeout ─► your fallback branch
```

Standby uses it to hold a seat until the attendee answers. The same shape works for anything
that needs a person to accept a slot: interview times, on-call swaps, equipment bookings,
shift cover, a delivery window.

## The three pieces

**1. The waiting task** (Amazon States Language)

```yaml
AskByCalendar:
  Type: Task
  Resource: arn:aws:states:::lambda:invoke.waitForTaskToken
  Parameters:
    FunctionName: ${SendInviteArn}
    Payload: { key.$: $.request_id, token.$: $$.Task.Token }
  TimeoutSecondsPath: $.answer_within_seconds   # Standard workflows only: Express caps at 5 min
  ResultPath: $.reply
  Catch:
    - { ErrorEquals: [States.Timeout], ResultPath: $.error, Next: NoAnswer }
    - { ErrorEquals: [States.ALL],     ResultPath: $.error, Next: CouldNotAsk }
  Next: Answered          # Choice on $.reply.partstat
```

**2. Sending the invite** — store the token, then send `METHOD:REQUEST` with `UID = <key>@<your-domain>`
and `ORGANIZER` = an address on a domain whose MX points at SES. `src/ical.py` builds both the
calendar part and the MIME message (`build_calendar`, `build_invite_email`).

**3. Reading the answer** — an SES receipt rule (S3 action, then Lambda action) hands you the raw
mail. `parse_reply_email()` returns the UID, the attendee and their `PARTSTAT`:

```python
reply, sender = parse_reply_email(raw_bytes)
if reply and reply.attendee == expected_attendee(reply.uid):
    sfn.send_task_success(taskToken=token_for(reply.uid),
                          output=json.dumps({"partstat": reply.partstat}))
```

## Things that bit us (so they don't bite you)

- **Reply lines are folded.** The `ATTENDEE` line that carries `PARTSTAT` is usually longer than
  75 octets and arrives split across lines. Unfold before you read it (`ical.unfold`).
- **Map replies by UID, not by subject or sender.** One UID per request makes a reply point at
  exactly one waiting task. Check that the replying attendee is the one you invited; ignore the rest.
- **Ignore what you don't expect.** `TENTATIVE`, replies to finished requests and unknown UIDs are
  dropped, so a late or forged reply cannot resume anything. A token that already timed out makes
  `SendTaskSuccess` throw `TaskTimedOut`; catch it.
- **Cancel what you take back.** If the request is withdrawn (timeout, someone else got it), send
  `METHOD:CANCEL` with the same UID and a higher `SEQUENCE`, and the event leaves their calendar.
- **Catch send failures, not just timeouts.** If the invite can't be sent, the task fails; route
  `States.ALL` to a branch that frees whatever you were holding.
- **SES receiving needs a domain you control.** A subdomain is enough (`MX 10 inbound-smtp.<region>.amazonaws.com`),
  and it leaves the parent domain's mail alone. Receiving is only offered in some regions.
- **Sandbox mode is a feature for demos.** Until you request production access, SES only sends to
  verified addresses, so nobody else can be invited by accident.

## Tested with

Google Calendar (Gmail and Google Workspace accounts): Yes, No and no answer, with cancellations
removing the event from the declined attendee's calendar. Other calendar clients use the same
iMIP standard (RFC 6047) but have not been tested here.
