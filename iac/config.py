"""
Address plan and stack settings.

Every CIDR the stack uses is declared here rather than inline at the resource.
Routing is what this assignment is assessed on, and routing.md plus evidence
files 03/04/05 have to agree with each other — which they can only do if there
is exactly one source of truth for the numbers.

The three VPCs do not overlap, and each is a /16 whose second octet identifies
it: 10.0.x is egress, 10.1.x is app, 10.2.x is data. That makes a TGW route
table readable at a glance.

If the assignment brief mandates specific CIDRs or a different VPC count,
change them here and nothing else moves.
"""

import pulumi

_cfg = pulumi.Config()

# ---------------------------------------------------------------------------
# Identity
#
# The student token threads through everything: the app's /whoami response, the
# S3 key prefix the Lambda writes under, and the Name tag on every resource, so
# the inventory in evidence file 02 is attributable to one person.
#
#   pulumi config set studentToken <your token>
# ---------------------------------------------------------------------------
STUDENT_TOKEN = _cfg.require("studentToken")
PREFIX = f"{STUDENT_TOKEN}-orders"

# ---------------------------------------------------------------------------
# Placement
#
# AZs are named explicitly rather than discovered. Lab accounts sometimes
# restrict which AZs may be used, and a silent reshuffle between runs would put
# app-a in a different AZ than the evidence claims.
#
# Read from the aws namespace, not the project's own: _cfg.get("aws:region")
# would look for a key literally named "iac:aws:region".
# ---------------------------------------------------------------------------
REGION = pulumi.Config("aws").get("region") or "ap-southeast-1"
AZ_A = _cfg.get("azA") or f"{REGION}a"
AZ_B = _cfg.get("azB") or f"{REGION}b"

# ---------------------------------------------------------------------------
# Egress VPC — 10.0.0.0/16
#
# Owns the only NAT gateway and the only internet-bound path for the private
# tiers. No workload runs here; it is pure transit.
# ---------------------------------------------------------------------------
EGRESS_VPC_CIDR = "10.0.0.0/16"
EGRESS_PUBLIC_A = "10.0.0.0/24"   # NAT gateway lives here
EGRESS_PUBLIC_B = "10.0.1.0/24"
EGRESS_TGW_A = "10.0.100.0/28"    # TGW ENIs only, hence the /28
EGRESS_TGW_B = "10.0.101.0/28"

# ---------------------------------------------------------------------------
# App VPC — 10.1.0.0/16
#
# The public subnet holds the bastion and the nginx edge, both of which take
# inbound connections from outside. The app instances are private and reach the
# internet only via 0.0.0.0/0 -> TGW -> egress VPC NAT.
#
# The trap: giving the app subnets their own NAT, or an IGW route, makes
# evidence file 08 report a local address instead of the egress NAT's EIP. The
# collector calls that the single most important line in the archive.
# ---------------------------------------------------------------------------
APP_VPC_CIDR = "10.1.0.0/16"
APP_PUBLIC_A = "10.1.0.0/24"      # bastion, nginx edge
APP_PUBLIC_B = "10.1.1.0/24"      # spare, so the public tier is not AZ-locked
APP_PRIVATE_A = "10.1.10.0/24"    # app-a
APP_PRIVATE_B = "10.1.11.0/24"    # app-b
APP_TGW_A = "10.1.100.0/28"
APP_TGW_B = "10.1.101.0/28"

# ---------------------------------------------------------------------------
# Data VPC — 10.2.0.0/16
#
# Postgres on EC2, with no IGW and no NAT of its own. Outbound internet (for
# `dnf install postgresql-server`) goes through the same central egress path as
# everything else; "isolated" here means nothing may reach *in*.
# ---------------------------------------------------------------------------
DATA_VPC_CIDR = "10.2.0.0/16"
DATA_PRIVATE_A = "10.2.10.0/24"   # db
DATA_PRIVATE_B = "10.2.11.0/24"   # spare
DATA_TGW_A = "10.2.100.0/28"
DATA_TGW_B = "10.2.101.0/28"

ANY = "0.0.0.0/0"

# The two subnets that may talk to the database. Used as CIDRs rather than as a
# security-group reference, because SG-to-SG references do not work across a
# Transit Gateway — see the note in compute.py.
APP_PRIVATE_CIDRS = [APP_PRIVATE_A, APP_PRIVATE_B]

# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------
INSTANCE_TYPE = _cfg.get("instanceType") or "t3.micro"

# Amazon Linux 2023, resolved through SSM at deploy time rather than pinned, so
# the stack does not rot when AWS replaces the AMI.
AMI_SSM_PARAM = (
    "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
)

APP_PORT = 8000
DB_PORT = 5432

# Who may SSH to the bastion and reach the edge on port 80.
#
# Defaulted wide because the evidence collector runs from a lab VM whose public
# address changes between sessions, and a stale CIDR here locks you out of your
# own stack mid-assessment. Narrow it with
#   pulumi config set sshCidr <your.ip>/32
# if your address is stable.
SSH_CIDR = _cfg.get("sshCidr") or ANY

# ---------------------------------------------------------------------------
# Tags
#
# Applied to every resource, so a shared lab account stays attributable and
# teardown can find everything.
# ---------------------------------------------------------------------------
BASE_TAGS = {
    "Project": "orders-api-capstone",
    "Student": STUDENT_TOKEN,
    "ManagedBy": "pulumi",
}


def tags(name: str, **extra: str) -> dict:
    """Standard tags plus a Name — which is what evidence file 02 reads."""
    return {**BASE_TAGS, "Name": f"{PREFIX}-{name}", **extra}
