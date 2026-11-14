"""What one event costs, from measured per-step usage and us-east-1 list prices.

Usage per step comes from this project's own runs (Nov 13-14 2026): Step Functions state counts
from execution histories, Lambda billed duration from CloudWatch REPORT lines. Prices were read
from the AWS pricing pages on Nov 14 2026 (links in results/README.md). Free tiers are ignored,
so this is what it costs once the free tiers are used up.

  python3 scripts/cost_model.py --seats 50 --signups 80
"""
import argparse

PRICE = {
    "sfn_transition": 0.025 / 1000,          # Step Functions Standard, per state transition
    "lambda_request": 0.20 / 1_000_000,      # Lambda, per request
    "lambda_gb_s": 0.0000133334,             # Lambda, Arm, per GB-second
    "api_request": 1.00 / 1_000_000,         # API Gateway HTTP API, per request
    "ses_out": 0.10 / 1000,                  # SES, per outbound email
    "ses_in": 0.10 / 1000,                   # SES receiving, per inbound email (replies are < 256 KB: no chunk charge)
    "ddb_wru": 0.625 / 1_000_000,            # DynamoDB on-demand, per write request unit
    "ddb_rru": 0.125 / 1_000_000,            # DynamoDB on-demand, per read request unit
    "s3_put": 0.005 / 1000,                  # S3 Standard, per PUT
}
MEM_GB = 0.25  # every function runs with 256 MB
# Measured average billed duration per invocation, ms (CloudWatch Logs Insights over the test runs)
MS = {"api": 106, "invite": 88, "inbound": 786, "confirm": 737, "release": 293, "reconfirm_all": 660, "lock": 692}
# Measured state transitions per execution (Step Functions execution histories)
TRANSITIONS = {"answered": 3, "expired": 2, "event": 5}
# Rough DynamoDB units per person per hold, counted from the code paths (transactional writes cost 2 WRU/item)
WRU_PER_HOLD, RRU_PER_HOLD = 20, 20


def model(seats, signups, no_on_reconfirm, polls):
    offers = seats + no_on_reconfirm          # first offers + seats re-offered after the reconfirm round
    reconfirms = seats
    replies = offers + (seats - no_on_reconfirm)  # every offer answered Yes; reconfirm: some stay silent
    lam = {
        "api": signups + 1 + polls, "invite": offers + reconfirms, "inbound": replies,
        "confirm": offers + (seats - no_on_reconfirm), "release": no_on_reconfirm,
        "reconfirm_all": 1, "lock": 1,
    }
    cost = {
        "Step Functions": (offers * TRANSITIONS["answered"] + (seats - no_on_reconfirm) * TRANSITIONS["answered"]
                           + no_on_reconfirm * TRANSITIONS["expired"] + TRANSITIONS["event"]) * PRICE["sfn_transition"],
        "SES (out: invites, asks, cancellations)": (offers + reconfirms + no_on_reconfirm) * PRICE["ses_out"],
        "SES (in: replies)": replies * PRICE["ses_in"],
        "Lambda": sum(n * PRICE["lambda_request"] + n * MS[k] / 1000 * MEM_GB * PRICE["lambda_gb_s"] for k, n in lam.items()),
        "API Gateway": lam["api"] * PRICE["api_request"],
        "DynamoDB": (offers + reconfirms) * (WRU_PER_HOLD * PRICE["ddb_wru"] + RRU_PER_HOLD * PRICE["ddb_rru"]),
        "S3 (raw replies)": replies * PRICE["s3_put"],
    }
    return cost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seats", type=int, default=50)
    ap.add_argument("--signups", type=int, default=80)
    ap.add_argument("--silent", type=int, default=10, help="confirmed people who don't answer the reconfirm")
    ap.add_argument("--polls", type=int, default=0, help="board refreshes (every 2 s while open)")
    a = ap.parse_args()
    cost = model(a.seats, a.signups, a.silent, a.polls)
    print(f"{a.signups} sign-ups, {a.seats} seats, {a.silent} silent at reconfirm, {a.polls} board polls")
    print("| Service | USD |\n|---|---|")
    for k, v in cost.items():
        print(f"| {k} | {v:.4f} |")
    print(f"| **Total** | **{sum(cost.values()):.4f}** |")


if __name__ == "__main__":
    main()
