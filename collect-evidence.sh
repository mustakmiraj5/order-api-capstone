#!/usr/bin/env bash
#
# collect-evidence.sh
#
# Run this ONCE, at the end of your final session, while everything is still
# running. It gathers every proof this assessment requires in a single pass,
# packages them, and uploads the package to the submission bucket.
#
# Then destroy your stack.
#
# ---------------------------------------------------------------------------
# Why it works this way
#
# Your environment is destroyed when you finish, so nobody can re-test your
# endpoints later. That means the only record of your work is what this script
# captures. Two consequences:
#
#   1. Run it LAST, after everything passes. If a check fails here, it is
#      recorded as failing. There is no second attempt on a live system.
#
#   2. Every output lands in one archive, produced in one pass, on one clock.
#      The instance IDs, IP addresses, route table IDs and timestamps in these
#      files all have to agree with each other. Assembled output does not
#      agree with itself, which is the point.
#
# The script also performs the failover test itself — stopping and restarting
# your service on app-a — so you do not have to, and so the method is the same
# for everyone.
# ---------------------------------------------------------------------------
#
# Usage:
#   export STUDENT_TOKEN=...            # your assigned token
#   export SESSION_NONCE=...            # issued by your instructor at session start
#   export DUMP_BUCKET=...              # the bucket your Lambda writes to
#   export SSH_KEY=~/.ssh/yourkey.pem
#   export BASTION_IP=...               # public IP of your bastion
#   export EDGE_IP=...                  # public IP of your Nginx host
#   export APP_A_IP=... APP_B_IP=...    # private IPs
#   export DB_IP=... DB_PORT=5432
#   export LAMBDA_NAME=...
#   export TGW_RT_ID=...                # your TGW route table id
#   export PULUMI_DIR=./iac
#
#   ./collect-evidence.sh

set -uo pipefail   # deliberately NOT -e: a failing check is data, not a crash

need() { : "${!1:?environment variable $1 is not set}"; }
for v in STUDENT_TOKEN SESSION_NONCE DUMP_BUCKET SSH_KEY BASTION_IP \
         EDGE_IP APP_A_IP APP_B_IP DB_IP DB_PORT LAMBDA_NAME TGW_RT_ID; do
  need "$v"
done

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT="evidence-${STUDENT_TOKEN}-${RUN_ID}"
mkdir -p "$OUT"

SSHOPT=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
        -o LogLevel=ERROR -o ConnectTimeout=10)
JUMP=(-o "ProxyCommand=ssh ${SSHOPT[*]} -W %h:%p ec2-user@${BASTION_IP}")

on_bastion() { ssh "${SSHOPT[@]}" "ec2-user@${BASTION_IP}" "$@"; }
on_edge()    { ssh "${SSHOPT[@]}" "ec2-user@${EDGE_IP}" "$@"; }
on_app()     { local ip="$1"; shift; ssh "${SSHOPT[@]}" "${JUMP[@]}" "ec2-user@${ip}" "$@"; }

# cap <filename> <human label> <command...>
# Records the command, an ISO-8601 UTC timestamp, the exit code, and all output.
cap() {
  local f="$OUT/$1"; shift
  local label="$1"; shift
  {
    echo "### $label"
    echo "### utc:  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "### cmd:  $*"
    echo "---"
  } >> "$f"
  "$@" >> "$f" 2>&1
  local rc=$?
  echo "--- exit: $rc" >> "$f"
  printf '%-46s exit=%s\n' "$label" "$rc"
  return 0
}

echo "run id: $RUN_ID"
echo

# ==========================================================================
# 1. Identity and inventory
# ==========================================================================
cap 01-identity.txt   "caller identity"        aws sts get-caller-identity
cap 01-identity.txt   "region"                 aws configure get region
cap 02-inventory.json "instances"              aws ec2 describe-instances \
  --filters "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].{Id:InstanceId,Tags:Tags,AZ:Placement.AvailabilityZone,Subnet:SubnetId,Private:PrivateIpAddress,Public:PublicIpAddress,SGs:SecurityGroups[].GroupId}'

# ==========================================================================
# 2. Routing — the core of the assessment
# ==========================================================================
cap 03-route-tables.json "vpc route tables"    aws ec2 describe-route-tables
cap 04-tgw-routes.json   "tgw routes"          aws ec2 search-transit-gateway-routes \
  --transit-gateway-route-table-id "$TGW_RT_ID" --filters "Name=state,Values=active"
cap 05-tgw-attach.json   "tgw attachments"     aws ec2 describe-transit-gateway-attachments
cap 06-nat.json          "nat gateways"        aws ec2 describe-nat-gateways \
  --filter "Name=state,Values=available"
cap 07-sgs.json          "security groups"     aws ec2 describe-security-groups

# ==========================================================================
# 3. Centralized egress
#
# The address app-a sees itself as must be the egress VPC NAT's EIP. This is
# the single most important line in the archive.
# ==========================================================================
cap 08-egress.txt "app-a public egress address" \
  on_app "$APP_A_IP" "curl -s --max-time 8 https://checkip.amazonaws.com"
cap 08-egress.txt "app-b public egress address" \
  on_app "$APP_B_IP" "curl -s --max-time 8 https://checkip.amazonaws.com"
cap 08-egress.txt "nat gateway elastic ips" \
  aws ec2 describe-nat-gateways --filter "Name=state,Values=available" \
    --query 'NatGateways[].NatGatewayAddresses[].PublicIp'

# ==========================================================================
# 4. Isolation — the database must not be reachable from the public subnet
# ==========================================================================
cap 09-isolation.txt "db port from bastion (must fail)" \
  on_bastion "timeout 6 bash -c '</dev/tcp/${DB_IP}/${DB_PORT}' && echo OPEN || echo CLOSED_OR_TIMEOUT"

# ==========================================================================
# 5. Service management
# ==========================================================================
for host_var in APP_A_IP APP_B_IP; do
  ip="${!host_var}"
  cap 10-systemd.txt "$host_var unit file" \
    on_app "$ip" "cat /etc/systemd/system/orders-api.service"
  cap 10-systemd.txt "$host_var enabled at boot" \
    on_app "$ip" "systemctl is-enabled orders-api"
  cap 10-systemd.txt "$host_var status" \
    on_app "$ip" "systemctl status orders-api --no-pager"
  cap 10-systemd.txt "$host_var process owner" \
    on_app "$ip" "ps -eo user,cmd | grep -v grep | grep orders"
  cap 10-systemd.txt "$host_var credentials not in environment dump" \
    on_app "$ip" "systemctl show orders-api -p Environment"
done

# ==========================================================================
# 6. Load balancing
# ==========================================================================
cap 11-nginx.txt "nginx effective config" on_edge "sudo nginx -T"

cap 12-loadbalancing.txt "ten requests across the pool" \
  bash -c "for i in \$(seq 1 10); do curl -s --max-time 8 http://${EDGE_IP}/whoami; echo; done"
cap 12-loadbalancing.txt "database-backed endpoint" \
  bash -c "curl -s --max-time 8 http://${EDGE_IP}/orders"

# ==========================================================================
# 7. Failover — performed by this script, not by you
# ==========================================================================
cap 13-failover.txt "stopping orders-api on app-a" \
  on_app "$APP_A_IP" "sudo systemctl stop orders-api && systemctl is-active orders-api"
sleep 8
cap 13-failover.txt "six requests with app-a down" \
  bash -c "for i in \$(seq 1 6); do curl -s -o /dev/null -w '%{http_code} ' --max-time 8 http://${EDGE_IP}/whoami; done; echo"
cap 13-failover.txt "which instance answered" \
  bash -c "for i in \$(seq 1 3); do curl -s --max-time 8 http://${EDGE_IP}/whoami; echo; done"
cap 13-failover.txt "restarting orders-api on app-a" \
  on_app "$APP_A_IP" "sudo systemctl start orders-api && sleep 5 && systemctl is-active orders-api"
sleep 12
cap 13-failover.txt "ten requests after recovery" \
  bash -c "for i in \$(seq 1 10); do curl -s --max-time 8 http://${EDGE_IP}/whoami; echo; done"

# ==========================================================================
# 8. Automation
#
# head-object carries a server-side LastModified and ETag. Those are written
# by S3, not by you, which is why they are collected rather than pasted.
# ==========================================================================
cap 14-lambda.json "function configuration" \
  aws lambda get-function-configuration --function-name "$LAMBDA_NAME"
cap 14-lambda.json "invocation on demand" \
  aws lambda invoke --function-name "$LAMBDA_NAME" --log-type Tail \
    --query LogResult --output text "$OUT/lambda-invoke-payload.json"

LOG_GROUP="/aws/lambda/${LAMBDA_NAME}"
STREAM=$(aws logs describe-log-streams --log-group-name "$LOG_GROUP" \
  --order-by LastEventTime --descending --max-items 1 \
  --query 'logStreams[0].logStreamName' --output text 2>/dev/null)
cap 15-cloudwatch.json "most recent log stream: $STREAM" \
  aws logs get-log-events --log-group-name "$LOG_GROUP" --log-stream-name "$STREAM"

cap 16-dump.json "objects in dump bucket" \
  aws s3api list-objects-v2 --bucket "$DUMP_BUCKET" --prefix "$STUDENT_TOKEN"
LATEST=$(aws s3api list-objects-v2 --bucket "$DUMP_BUCKET" --prefix "$STUDENT_TOKEN" \
  --query 'sort_by(Contents,&LastModified)[-1].Key' --output text 2>/dev/null)
cap 16-dump.json "server-side metadata for $LATEST" \
  aws s3api head-object --bucket "$DUMP_BUCKET" --key "$LATEST"

# ==========================================================================
# 9. Declared state
# ==========================================================================
if [ -n "${PULUMI_DIR:-}" ] && [ -d "$PULUMI_DIR" ]; then
  cap 17-pulumi.json "stack export" bash -c "cd '$PULUMI_DIR' && pulumi stack export"
  cap 18-pulumi-preview.txt "preview against current state" \
    bash -c "cd '$PULUMI_DIR' && pulumi preview --diff --non-interactive"
fi

# ==========================================================================
# 10. Manifest
# ==========================================================================
{
  echo "student_token: $STUDENT_TOKEN"
  echo "session_nonce: $SESSION_NONCE"
  echo "run_id:        $RUN_ID"
  echo "started_utc:   $RUN_ID"
  echo "finished_utc:  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host:          $(uname -srm)"
  echo
  echo "sha256:"
  (cd "$OUT" && sha256sum ./* 2>/dev/null | sort -k2)
} > "$OUT/00-manifest.txt"

# The archive is committed to your repository, not uploaded. Extract it in
# place so a marker can read individual files in the browser.
rm -rf evidence && mv "$OUT" evidence

echo
echo "written to ./evidence/  ($(ls evidence | wc -l) files)"
echo
echo "Check evidence/08-egress.txt now. If app-a's address is not your egress"
echo "NAT's EIP, fix it and re-run before you destroy anything."
echo
echo "Then, BEFORE teardown:"
echo "    git add evidence && git commit -m \"evidence: $RUN_ID\" && git push"
