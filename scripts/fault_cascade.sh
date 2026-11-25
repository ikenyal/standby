#!/bin/bash
# Controlled fault injection for H2: the Release function cannot send email, every other part works.
#
#   scripts/fault_cascade.sh <label> <out.jsonl> [trials]
#
# Adds an inline Deny on ses:SendEmail / ses:SendRawEmail to the Release function's role ONLY,
# runs scripts/cascade.py, then removes the Deny. Removal runs from an EXIT/INT/TERM trap, so it
# happens even if a trial fails or the run is interrupted; afterwards it verifies the policy is gone.
#
# Every CANCEL the release step sends now fails with AccessDenied, which the SDK does not retry, so
# the step fails after freeing the seat and Step Functions retries it: exactly the H2 path, the same
# way for the old and the fixed code. Needs STANDBY_API / STANDBY_API_KEY (.env) and two profiles:
#   WRITE_PROFILE (default brightflow-sandbox-ikeda)          put/delete the inline policy only
#   READ_PROFILE  (default brightflow-sandbox-ikeda-readonly) everything else
set -euo pipefail
cd "$(dirname "$0")/.."
LABEL=${1:?label}; OUT=${2:?out.jsonl}; TRIALS=${3:-3}
WRITE_PROFILE=${WRITE_PROFILE:-brightflow-sandbox-ikeda}
READ_PROFILE=${READ_PROFILE:-brightflow-sandbox-ikeda-readonly}
STACK=${STACK:-standby}
POLICY_NAME=h2-fault-injection-deny-ses
POLICY_FILE=scripts/fault/deny-ses-release.json
PY=${PY:-python3}  # needs boto3 for the Step Functions counts (e.g. the SAM CLI's python)
export AWS_REGION=${AWS_REGION:-us-east-1} AWS_DEFAULT_REGION=${AWS_REGION:-us-east-1} AWS_PAGER=""
set -a; [ -f .env ] && . ./.env; set +a

ro() { AWS_PROFILE=$READ_PROFILE aws "$@"; }
rw() { AWS_PROFILE=$WRITE_PROFILE aws "$@"; }
log() { echo "[fault $(date -u +%H:%M:%SZ)] $*"; }

ROLE=$(ro cloudformation describe-stack-resource --stack-name "$STACK" --logical-resource-id ReleaseFunctionRole \
  --query StackResourceDetail.PhysicalResourceId --output text)
STATE_MACHINE_ARN=$(ro cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='StateMachineArn'].OutputValue" --output text)
export STATE_MACHINE_ARN
[ -n "$ROLE" ] && [ -n "$STATE_MACHINE_ARN" ] || { echo "could not resolve role / state machine" >&2; exit 1; }
case "$ROLE" in *ReleaseFunctionRole*) ;; *) echo "refusing: $ROLE is not the Release function role" >&2; exit 1;; esac

remove_deny() {
  local rc=$?
  trap - EXIT INT TERM
  log "removing $POLICY_NAME from $ROLE"
  for i in 1 2 3 4 5; do
    rw iam delete-role-policy --role-name "$ROLE" --policy-name "$POLICY_NAME" 2>/dev/null && break
    ro iam list-role-policies --role-name "$ROLE" --query PolicyNames --output text | grep -qw "$POLICY_NAME" || break
    sleep 2
  done
  if ro iam list-role-policies --role-name "$ROLE" --query PolicyNames --output text | grep -qw "$POLICY_NAME"; then
    log "ERROR: $POLICY_NAME is STILL attached to $ROLE; remove it by hand:"
    log "  aws iam delete-role-policy --profile $WRITE_PROFILE --role-name $ROLE --policy-name $POLICY_NAME"
    exit 2
  fi
  log "verified: $POLICY_NAME is gone; inline policies now: $(ro iam list-role-policies --role-name "$ROLE" --query PolicyNames --output text)"
  exit $rc
}
trap remove_deny EXIT INT TERM

log "role $ROLE; inline policies before: $(ro iam list-role-policies --role-name "$ROLE" --query PolicyNames --output text)"
rw iam put-role-policy --role-name "$ROLE" --policy-name "$POLICY_NAME" --policy-document "file://$POLICY_FILE"
log "applied $POLICY_NAME"
for action in ses:SendRawEmail ses:SendEmail; do
  d=$(rw iam simulate-principal-policy --policy-source-arn "$(ro iam get-role --role-name "$ROLE" --query Role.Arn --output text)" \
      --action-names "$action" --query 'EvaluationResults[0].EvalDecision' --output text)
  log "simulated $action on the Release role: $d"
  [ "$d" = "explicitDeny" ] || { log "deny not in effect; aborting"; exit 1; }
done
log "waiting 30 s for IAM propagation"
sleep 30

AWS_PROFILE=$READ_PROFILE "$PY" scripts/cascade.py --label "$LABEL" --trials "$TRIALS" --people 30 --capacity 10 --hold 60 --out "$OUT"
log "trials done"
