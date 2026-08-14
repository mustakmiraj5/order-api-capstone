"""
Phase 3 — compute and security groups.

Five instances and the security-group graph that decides who may talk to whom:

    bastion   app VPC,  public   22 from SSH_CIDR
    edge      app VPC,  public   80 from anywhere, 22 from bastion
    app-a     app VPC,  private, AZ a   8000 from edge, 22 from bastion
    app-b     app VPC,  private, AZ b   8000 from edge, 22 from bastion
    db        data VPC, private         5432 and 22 from the app subnets only

app-a and app-b are deliberately in different AZs, so the failover evidence
shows a genuine cross-AZ survival rather than two processes on one host.

Two things here are less obvious than they look.

**Security groups cannot be referenced across a Transit Gateway.** SG-to-SG
rules only work inside one VPC (or across a peering connection with referencing
enabled). The database is in a different VPC reached over the TGW, so its rules
have to be written as CIDRs — config.APP_PRIVATE_CIDRS. Writing them as a
source security group would be silently accepted by neither AWS nor the intent.

**The database is not reachable from the bastion at all**, by routing as well as
by security group. That is the requirement, but it also means you cannot SSH to
the database directly to install Postgres in phase 5. The path is a double jump,
bastion -> app-a -> db:

    ssh -J ec2-user@$BASTION_IP,ec2-user@$APP_A_IP ec2-user@$DB_IP

The instance profile also grants SSM Session Manager, so `aws ssm start-session`
works as a fallback if the SSH chain gives trouble.

No user_data here. Instances come up bare and are configured over SSH in phases
4 through 6. Configuration is still scripted and reproducible, and keeping it
out of user_data means a fix does not require replacing the instance — which
would change the private IPs the evidence archive has to agree on.
"""

import json
from dataclasses import dataclass

import pulumi_aws as aws
import pulumi_tls as tls

import config


@dataclass
class Compute:
    """Instance and security-group handles the later phases need."""

    key: tls.PrivateKey
    keypair: aws.ec2.KeyPair
    role: aws.iam.Role
    profile: aws.iam.InstanceProfile
    bastion_sg: aws.ec2.SecurityGroup
    edge_sg: aws.ec2.SecurityGroup
    app_sg: aws.ec2.SecurityGroup
    db_sg: aws.ec2.SecurityGroup
    bastion: aws.ec2.Instance
    edge: aws.ec2.Instance
    app_a: aws.ec2.Instance
    app_b: aws.ec2.Instance
    db: aws.ec2.Instance


def build(net) -> Compute:
    # =======================================================================
    # 1. SSH keypair
    #
    # Generated here rather than created by hand, so the private half lives in
    # Pulumi's encrypted state. A wiped lab VM recovers SSH access with
    # `pulumi stack output sshPrivateKey --show-secrets` instead of needing a
    # .pem file that died with the VM.
    # =======================================================================
    key = tls.PrivateKey("ssh-key", algorithm="RSA", rsa_bits=4096)
    keypair = aws.ec2.KeyPair(
        "ssh-keypair",
        key_name=f"{config.PREFIX}-key",
        public_key=key.public_key_openssh,
        tags=config.tags("ssh-keypair"),
    )

    # =======================================================================
    # 2. AMI
    #
    # Resolved through SSM at deploy time rather than pinned, so the stack does
    # not rot when AWS replaces the image.
    # =======================================================================
    ami_id = aws.ssm.get_parameter(name=config.AMI_SSM_PARAM).value

    # =======================================================================
    # 3. Instance profile
    #
    # Session Manager access for all five hosts. Phase 4 extends this role with
    # read access to the SSM parameter holding the database password, so that
    # the credential never has to pass through user-data — which is readable by
    # any process on the instance via IMDS.
    # =======================================================================
    role = aws.iam.Role(
        "instance-role",
        assume_role_policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        }),
        tags=config.tags("instance-role"),
    )
    aws.iam.RolePolicyAttachment(
        "instance-role-ssm",
        role=role.name,
        policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    )
    profile = aws.iam.InstanceProfile(
        "instance-profile", role=role.name, tags=config.tags("instance-profile"))

    # =======================================================================
    # 4. Security groups
    # =======================================================================
    # Egress is unrestricted everywhere. That is intentional: the private tiers
    # already have exactly one way out, forced by routing, and restricting it
    # again here would only obscure where the constraint actually lives.
    allow_all_egress = [{
        "protocol": "-1", "from_port": 0, "to_port": 0,
        "cidr_blocks": [config.ANY],
        "description": "all outbound",
    }]

    bastion_sg = aws.ec2.SecurityGroup(
        "bastion-sg",
        vpc_id=net.app_vpc.id,
        description="bastion: ssh from the operator",
        ingress=[{
            "protocol": "tcp", "from_port": 22, "to_port": 22,
            "cidr_blocks": [config.SSH_CIDR],
            "description": "ssh",
        }],
        egress=allow_all_egress,
        tags=config.tags("bastion-sg"),
    )

    edge_sg = aws.ec2.SecurityGroup(
        "edge-sg",
        vpc_id=net.app_vpc.id,
        description="nginx edge: http from the world, ssh via bastion",
        ingress=[
            {
                "protocol": "tcp", "from_port": 80, "to_port": 80,
                "cidr_blocks": [config.ANY],
                "description": "http from anywhere",
            },
            {
                "protocol": "tcp", "from_port": 22, "to_port": 22,
                "security_groups": [bastion_sg.id],
                "description": "ssh from the bastion only",
            },
        ],
        egress=allow_all_egress,
        tags=config.tags("edge-sg"),
    )

    app_sg = aws.ec2.SecurityGroup(
        "app-sg",
        vpc_id=net.app_vpc.id,
        description="app tier: gunicorn from the edge, ssh via bastion",
        ingress=[
            {
                "protocol": "tcp",
                "from_port": config.APP_PORT, "to_port": config.APP_PORT,
                "security_groups": [edge_sg.id],
                # Not open to the VPC — only nginx may reach gunicorn, so the
                # app tier cannot be hit directly even from inside.
                "description": "gunicorn from the nginx edge only",
            },
            {
                "protocol": "tcp", "from_port": 22, "to_port": 22,
                "security_groups": [bastion_sg.id],
                "description": "ssh from the bastion only",
            },
        ],
        egress=allow_all_egress,
        tags=config.tags("app-sg"),
    )

    # The isolation requirement, in rule form.
    #
    # CIDRs rather than a source security group, because app_sg lives in a
    # different VPC and SG references do not cross a Transit Gateway.
    #
    # Note the absence of the public subnet 10.1.0.0/24. Even if a route to the
    # data VPC were added to the app public route table tomorrow, the bastion
    # still could not open 5432 — which is what evidence file 09 records.
    db_sg = aws.ec2.SecurityGroup(
        "db-sg",
        vpc_id=net.data_vpc.id,
        description="postgres: reachable from the app subnets only",
        ingress=[
            {
                "protocol": "tcp",
                "from_port": config.DB_PORT, "to_port": config.DB_PORT,
                "cidr_blocks": config.APP_PRIVATE_CIDRS,
                "description": "postgres from app-a and app-b only",
            },
            {
                "protocol": "tcp", "from_port": 22, "to_port": 22,
                "cidr_blocks": config.APP_PRIVATE_CIDRS,
                "description": "ssh via a jump through an app instance",
            },
        ],
        egress=allow_all_egress,
        tags=config.tags("db-sg"),
    )

    # =======================================================================
    # 5. Instances
    # =======================================================================
    def _instance(name, subnet, sg, public=False):
        return aws.ec2.Instance(
            name,
            ami=ami_id,
            instance_type=config.INSTANCE_TYPE,
            subnet_id=subnet.id,
            vpc_security_group_ids=[sg.id],
            key_name=keypair.key_name,
            iam_instance_profile=profile.name,
            associate_public_ip_address=public,
            # IMDSv2 required. app.py already speaks it — it asks for a token
            # before reading instance-id — and leaving v1 enabled would let any
            # process that can forge a request read instance metadata.
            metadata_options={
                "http_endpoint": "enabled",
                "http_tokens": "required",
                "http_put_response_hop_limit": 1,
            },
            tags=config.tags(name),
        )

    bastion = _instance("bastion", net.app_public_a, bastion_sg, public=True)
    edge = _instance("edge", net.app_public_a, edge_sg, public=True)
    app_a = _instance("app-a", net.app_private_a, app_sg)
    app_b = _instance("app-b", net.app_private_b, app_sg)
    db = _instance("db", net.data_private_a, db_sg)

    return Compute(
        key=key, keypair=keypair, role=role, profile=profile,
        bastion_sg=bastion_sg, edge_sg=edge_sg, app_sg=app_sg, db_sg=db_sg,
        bastion=bastion, edge=edge, app_a=app_a, app_b=app_b, db=db,
    )
