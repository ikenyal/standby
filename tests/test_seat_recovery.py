"""H2: seats must not stay empty when a step fails halfway and is retried.

Run: python3 tests/test_seat_recovery.py  (no AWS needed; exits 1 if any test fails)

The AWS SDK is replaced by stubs and DynamoDB by an in-memory model of the same state machine
(event seat counter, attendees, holds), patched onto the real `store` module. The tests drive
the real `steps` / `workflow` / `inbound` code and only check outcomes, so they hold for any
implementation that keeps the seat promise:

  1. A release step that fails after freeing the seat (e.g. SES throttles the CANCEL) and is
     retried by Step Functions must still offer the seat to the next person.
  2. If StartExecution fails right after a seat was taken, the seat must not be leaked forever:
     the next recovery pass must give that hold a workflow.
  3. A hold whose workflow ended without settling it must be released and its seat re-offered.
"""
import os
import sys
import traceback
import types
from pathlib import Path

# ---------------------------------------------------------------- AWS SDK stubs (no network)


class ClientError(Exception):
    def __init__(self, error_response, operation_name):
        super().__init__(f"{error_response['Error']['Code']} ({operation_name})")
        self.response = error_response


def _client_error(code: str, op: str = "Op") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class _Anything:
    def __init__(self, *a, **k): pass
    def __getattr__(self, _name): return _Anything()
    def __call__(self, *a, **k): return _Anything()


def _install_stubs():
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda *a, **k: _Anything()
    boto3.resource = lambda *a, **k: _Anything()
    dynamodb = types.ModuleType("boto3.dynamodb")
    conditions = types.ModuleType("boto3.dynamodb.conditions")
    conditions.Key = _Anything
    botocore = types.ModuleType("botocore")
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = ClientError
    config = types.ModuleType("botocore.config")
    config.Config = _Anything
    sys.modules.update({
        "boto3": boto3, "boto3.dynamodb": dynamodb, "boto3.dynamodb.conditions": conditions,
        "botocore": botocore, "botocore.exceptions": exceptions, "botocore.config": config,
    })


_install_stubs()
os.environ.setdefault("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:111122223333:stateMachine:standby-hold")
os.environ.setdefault("LIFECYCLE_ARN", "arn:aws:states:us-east-1:111122223333:stateMachine:standby-event")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import mailer  # noqa: E402
import steps  # noqa: E402
import store  # noqa: E402
import workflow  # noqa: E402

RELEASED = ("DECLINED", "EXPIRED", "FAILED")

# ---------------------------------------------------------------- in-memory DynamoDB model


class FakeStore:
    """Same rules as src/store.py: `seated` counts INVITED + CONFIRMED and never exceeds capacity."""

    def __init__(self, capacity: int):
        self.clock = 1_800_000_000
        self.event = {"event_id": "e1", "title": "Meetup", "capacity": capacity, "seated": 0,
                      "hold_seconds": 600, "roster_locked": False,
                      "starts_at": "2027-01-20T19:00:00+09:00", "ends_at": "2027-01-20T20:00:00+09:00"}
        self.attendees = []  # ordered by position
        self.holds = {}
        self.n = 0

    # helpers for the tests
    def add(self, email):
        self.attendees.append({"sk": f"ATT#{len(self.attendees) + 1:06d}", "pos": len(self.attendees) + 1,
                               "email": email, "name": email, "status": "WAITING"})

    def att(self, email):
        return next(a for a in self.attendees if a["email"] == email)

    # the store API used by steps / workflow / inbound
    def now(self):
        return self.clock

    def get_event(self, eid):
        return dict(self.event) if eid == "e1" else None

    def list_attendees(self, eid):
        return [dict(a) for a in self.attendees]

    def get_hold(self, hid):
        h = self.holds.get(hid)
        return dict(h) if h else None

    def seat_next(self, eid):
        if self.event["seated"] >= self.event["capacity"]:
            return None
        att = next((a for a in self.attendees if a["status"] == "WAITING"), None)
        if not att:
            return None
        self.n += 1
        hid = f"h{self.n}"
        self.event["seated"] += 1
        att.update(status="INVITED", hold_id=hid)
        self.holds[hid] = {"hold_id": hid, "event_id": eid, "att_sk": att["sk"], "email": att["email"],
                           "name": att["name"], "status": "PENDING", "sequence": 0, "created_at": self.clock}
        return dict(self.holds[hid])

    def set_hold_token(self, hid, token):
        assert self.holds[hid]["status"] in ("PENDING", "INVITED")
        self.holds[hid].update(task_token=token, status="INVITED")
        self.att(self.holds[hid]["email"])["status"] = "INVITED"

    def release(self, hid, reason):
        h = self.holds.get(hid)
        if not h or h["status"] in RELEASED:
            return None
        h["status"] = reason
        h["sequence"] += 1
        h.pop("task_token", None)
        self.att(h["email"])["status"] = reason
        self.event["seated"] -= 1
        return dict(h)

    def mark_cancel_sent(self, hid):
        self.holds[hid]["cancel_sent"] = self.clock

    def open_event_ids(self):
        return [] if self.event["roster_locked"] else ["e1"]


class FakeSfn:
    """Step Functions control plane: executions by name; start can be made to fail."""

    def __init__(self):
        self.executions = {}  # name -> status
        self.fail_next_start = None

    def start_execution(self, stateMachineArn, name, input):
        if self.fail_next_start:
            code, self.fail_next_start = self.fail_next_start, None
            raise _client_error(code, "StartExecution")
        if name in self.executions:
            raise _client_error("ExecutionAlreadyExists", "StartExecution")
        self.executions[name] = "RUNNING"
        return {"executionArn": f"{stateMachineArn}:{name}"}

    def describe_execution(self, executionArn):
        name = executionArn.rsplit(":", 1)[1]
        if name not in self.executions:
            raise _client_error("ExecutionDoesNotExist", "DescribeExecution")
        return {"status": self.executions[name]}


def _setup(capacity: int):
    fake, sfn, mail = FakeStore(capacity), FakeSfn(), {"cancel": [], "invite": [], "fail_cancel": 0}
    for name in dir(fake):
        if not name.startswith("_") and callable(getattr(fake, name)) and name not in ("add", "att"):
            setattr(store, name, getattr(fake, name))
    workflow._sfn = sfn

    def send_cancel(event, hold, reason):
        if mail["fail_cancel"]:
            mail["fail_cancel"] -= 1
            raise _client_error("TooManyRequestsException", "SendEmail")  # SES sandbox: ~1 msg/s
        mail["cancel"].append((hold["hold_id"], reason))
        return "msg"

    mailer.send_cancel = send_cancel
    mailer.send_invite = lambda event, hold: mail["invite"].append(hold["hold_id"]) or "msg"
    return fake, sfn, mail


def _state(fake, sfn):
    return (f"seated={fake.event['seated']}/{fake.event['capacity']}, "
            f"attendees={[(a['email'], a['status']) for a in fake.attendees]}, "
            f"holds={[(h['hold_id'], h['status']) for h in fake.holds.values()]}, "
            f"executions={sfn.executions}")


# ---------------------------------------------------------------- tests


def test_release_retry_still_promotes_next_person():
    """Release frees the seat, then SES throttles the CANCEL; Step Functions retries the task."""
    fake, sfn, mail = _setup(capacity=1)
    fake.add("ada@example.org"); fake.add("alan@example.org")
    workflow.fill("e1")  # ada gets the seat (hold h1)
    fake.set_hold_token("h1", "token-1")
    sfn.executions["h1"] = "RUNNING"

    mail["fail_cancel"] = 1
    try:
        steps.release({"hold_id": "h1", "reason": "EXPIRED"}, None)  # attempt 1
    except Exception:
        pass  # the task fails; the state machine's Retry (States.TaskFailed) runs it again
    steps.release({"hold_id": "h1", "reason": "EXPIRED"}, None)  # attempt 2 (retry)

    alan = fake.att("alan@example.org")
    assert alan["status"] == "INVITED" and alan.get("hold_id") in sfn.executions, \
        f"seat left empty after the retry: next person was never offered it. {_state(fake, sfn)}"
    assert fake.event["seated"] == 1, f"seat counter wrong: {_state(fake, sfn)}"
    assert ("h1", "EXPIRED") in mail["cancel"], f"released attendee never got the CANCEL: {mail['cancel']}"


def test_start_execution_failure_does_not_leak_seat():
    """seat_next commits, then StartExecution is throttled: the hold has no workflow."""
    fake, sfn, mail = _setup(capacity=1)
    fake.add("ada@example.org")
    sfn.fail_next_start = "ThrottlingException"
    try:
        workflow.fill("e1")
    except Exception:
        pass  # the API call fails; the organizer sees a 5xx
    # The seat is taken and nothing will ever expire or confirm it:
    assert fake.event["seated"] == 1 and fake.holds["h1"]["status"] == "PENDING" and not sfn.executions

    fake.clock += 300  # five minutes later, whatever recovery the system has runs
    reconcile = getattr(steps, "reconcile", None)
    assert reconcile is not None, \
        f"seat leaked: hold PENDING with no workflow and no recovery path exists. {_state(fake, sfn)}"
    reconcile({}, None)
    assert sfn.executions.get("h1") == "RUNNING", f"leaked hold was not re-driven: {_state(fake, sfn)}"
    assert fake.event["seated"] == 1, f"seat counter wrong: {_state(fake, sfn)}"


def test_hold_with_ended_workflow_is_released():
    """The release step exhausted its retries, so the execution FAILED and the hold is stuck INVITED."""
    fake, sfn, mail = _setup(capacity=1)
    fake.add("ada@example.org"); fake.add("alan@example.org")
    workflow.fill("e1")
    fake.set_hold_token("h1", "token-1")
    sfn.executions["h1"] = "FAILED"
    fake.clock += 300

    reconcile = getattr(steps, "reconcile", None)
    assert reconcile is not None, \
        f"seat leaked: hold INVITED, its workflow ended, no recovery path exists. {_state(fake, sfn)}"
    reconcile({}, None)
    assert fake.holds["h1"]["status"] == "EXPIRED", f"stuck hold not released: {_state(fake, sfn)}"
    alan = fake.att("alan@example.org")
    assert alan["status"] == "INVITED" and sfn.executions.get(alan.get("hold_id")) == "RUNNING", \
        f"freed seat not offered to the next person: {_state(fake, sfn)}"
    assert ("h1", "EXPIRED") in mail["cancel"], f"released attendee never got the CANCEL: {mail['cancel']}"


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("pass", name)
            except Exception as e:
                failed += 1
                print("FAIL", name)
                print("    " + "".join(traceback.format_exception_only(type(e), e)).strip())
    print(f"{failed} failed" if failed else "all passed")
    sys.exit(1 if failed else 0)
