# Well-Architected review: H2 before and after (excerpt)

This is an excerpt of two full AWS Well-Architected Framework reviews of this repository. Both were run with the
AWS Well-Architected Review skill against the live framework index
(`https://docs.aws.amazon.com/wellarchitected/latest/framework/toc-contents.json`). It covers only finding
**H2** and the follow-up note **M9** that the second review raised about the fix. The full reports also cover
security and account-level topics and are not published.

| | Before | After |
|---|---|---|
| Code reviewed | `d7095ba` | `347d806` (application code identical to `ba086be`) |
| Review date | 2026-11-25 | 2026-11-26 |
| Framework inventory | 7 sections (6 pillars + Cloud Foundations) · 57 questions · 261 best practices | same 261 best-practice IDs, so rows compare one to one |
| Overall maturity | 3 / 5 | 3 / 5 |
| Best practices Implemented | 54 / 261 | 59 / 261 |
| H2 best practices at High risk | 2 (REL04-BP04, REL11-BP03) | 0 |

Overall maturity stays at 3 / 5 because the remaining gaps in both reviews are operational (monitoring and
backups), not in the seat-handling design that H2 is about.

## H2 best practices, before and after

| BP ID | Best practice | Before | After |
|---|---|---|---|
| REL04-BP04 | Make mutating operations idempotent | Partially Implemented · **High** | **Implemented** |
| REL11-BP03 | Automate healing on all layers | Partially Implemented · **High** | **Implemented** |
| REL05-BP03 | Control and limit retry calls | Partially Implemented · Medium | **Implemented** |
| REL01-BP03 | Accommodate fixed service quotas and constraints through architecture | Partially Implemented · Low | **Implemented** |
| REL11-BP01 | Monitor all components of the workload to detect failures | Partially Implemented · Medium | Partially Implemented · Medium (the reconciler now detects lost and ended workflows; no alert yet) |
| REL05-BP01 | Implement graceful degradation to transform applicable hard dependencies into soft dependencies | Partially Implemented · Low | Partially Implemented · **Medium** (see M9) |

## Before: H2 as found (review of `d7095ba`)

*Line numbers refer to `d7095ba`.*

**H2: Seat-promotion side effects are not retry-safe, so seats can stay empty while people wait** (Reliability · High)

The database transitions are idempotent, but the work that follows them is not.

1. **Promotion lost on retry.** `steps.release` commits the release transaction and *then* sends the CANCEL
   email and calls `workflow.fill()` (`src/steps.py:30-37`). If either raises, Step Functions retries the task.
   On the retry `store.release` returns `None` because the hold is already released, so the cancel and
   `fill()` are **skipped**. The seat stays free with nobody invited. The same pattern exists in
   `inbound.handle_raw`.
2. **Orphaned holds.** `workflow.fill()` commits `seat_next` (which increments `seated`) and then calls
   `start_execution` (`src/workflow.py:35-43`). If that call fails, a `PENDING` hold occupies a seat with no
   execution to expire it. That seat is lost permanently.
3. **Offer withdrawn on SES throttling.** The invite task retries only Lambda errors. SES throttling falls
   through to the catch-all, which releases the seat as FAILED: that person silently loses their turn.
4. **Event lifecycle has no retry.** `LoadTimes` has no `Retry`, so one transient DynamoDB error means the event
   is never reconfirmed or locked.

- **Impact × Likelihood**: Moderate (seats left empty; this is the product's core promise) × High (SES's
  sandbox sending rate plus many holds expiring at once) → **High**
- **Recommendation**: always run the follow-up work on retry; make the CANCEL idempotent; retry
  `start_execution` (the execution name equals the hold id, so a duplicate start is safe); retry SES throttling
  before the catch; add `Retry` to `LoadTimes`; add a small reconciler that re-runs `fill()` and releases stale
  `PENDING` holds.

## After: H2 closed (review of `347d806`)

*Line numbers refer to `347d806`.*

**What was done**

1. `steps.release` always calls `workflow.settle_release`, which offers the seat to the next person first and
   then sends the CANCEL once (`src/steps.py:55-61`, `src/workflow.py:69-81`, `cancel_sent` in
   `src/store.py:329-334`).
2. `inbound` re-settles a retried DECLINED reply (`src/inbound.py:60-63`).
3. `StartExecution` is idempotent and retried adaptively (`src/workflow.py:19-43`).
4. SES throttling raises `RetryableSendError`, which the state machine retries with backoff and jitter before
   the catch (`src/steps.py:16-31`, `template.yaml:199-205`).
5. `LoadTimes` has a `Retry` (`template.yaml:326-330`).
6. A reconciler runs every 5 minutes (`template.yaml:366-390`, `src/workflow.py:119-156`). It restarts lost
   workflows, releases holds whose workflow ended, and offers free seats. It releases a hold only if the hold is
   still in the status it read, so it can never free a seat that was confirmed in the meantime
   (`src/workflow.py:150-152`).

**Verification**

- 3 unit tests fail on `d7095ba` and pass with the fix ([before](h2-tests-before.txt), [after](h2-tests-after.txt)).
  A fourth test covers the reconciler race that a review of the fix found ([race before](h2-race-before.txt)).
- Live cascade on the deployed stack, with the release step deliberately unable to send email
  (`scripts/fault_cascade.sh`): **10 of 10 seats empty in 3 of 3 trials before**, and **0 empty seats
  (30 of 30 people offered, settled in 185–190 s) in 3 of 3 trials after**. See [README.md §7](README.md).

## M9: new note from the after review (Reliability · Medium)

**A cancellation that still fails after the release step's retries is never sent.**
`settle_release` sends the CANCEL after offering the seat (`src/workflow.py:76-80`), which is the right order.
But if the send keeps failing, the execution ends FAILED with `cancel_sent` unset. The reconciler only finishes
off holds it releases itself (`src/workflow.py:131-136,152-154`), and `inbound` only re-settles when the attendee
replies again. In the after-fix fault run, 73 of 90 releases ended this way, and none were resent once the
fault was removed.

- **Impact × Likelihood**: Moderate (the released attendee keeps the event in their calendar; someone who confirmed
  earlier and missed the re-ask may assume they still have a seat) × Low (needs SES errors that outlast about a
  minute of retries) → **Medium**
- **Recommendation**: have the reconciler also resend cancellations for recently released holds without
  `cancel_sent`. The same UID and SEQUENCE make a duplicate harmless.
