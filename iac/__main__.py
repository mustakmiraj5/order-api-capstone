"""
orders-api capstone — Pulumi entry point.

Phases are added here as they land, so `pulumi up` stays runnable throughout
rather than being one large non-working commit.

    phase 2   network.py    VPCs, subnets, IGW, NAT, TGW, route tables   <- done
    phase 3   compute.py    bastion, edge, app-a, app-b, db + SGs        <- done
    phase 7   automation.py S3 dump bucket, Lambda, IAM, schedule

Exports feed scripts/make-env.sh, which turns them into the environment
variables collect-evidence.sh requires. Nothing the collector reads should ever
be typed by hand — hand-copied values are how the IDs in the archive end up
disagreeing with each other.
"""

import pulumi

import compute
import config
import network

net = network.build()
hosts = compute.build(net)

# ---------------------------------------------------------------------------
# Read by the evidence collector, via scripts/make-env.sh
# ---------------------------------------------------------------------------
pulumi.export("studentToken", config.STUDENT_TOKEN)
pulumi.export("tgwRtId", net.tgw_route_table.id)
pulumi.export("bastionIp", hosts.bastion.public_ip)
pulumi.export("edgeIp", hosts.edge.public_ip)
pulumi.export("appAIp", hosts.app_a.private_ip)
pulumi.export("appBIp", hosts.app_b.private_ip)
pulumi.export("dbIp", hosts.db.private_ip)
pulumi.export("dbPort", config.DB_PORT)

# The address app-a and app-b must report as their own in evidence file 08. If
# `curl checkip.amazonaws.com` from either one returns anything else, the
# centralized egress routing is wrong.
pulumi.export("natEip", net.nat_eip.public_ip)

# ---------------------------------------------------------------------------
# Recovery
#
# A secret output, so it is encrypted in stack state and masked in `pulumi
# stack output`. Retrieve it after a VM reset with:
#     pulumi stack output sshPrivateKey --show-secrets > ~/.ssh/orders.pem
#     chmod 600 ~/.ssh/orders.pem
# ---------------------------------------------------------------------------
pulumi.export("sshPrivateKey", pulumi.Output.secret(hosts.key.private_key_pem))

pulumi.export("phase", "3-compute")
