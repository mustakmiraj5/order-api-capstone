import os, urllib.request
from flask import Flask, jsonify
import psycopg2, psycopg2.extras

app = Flask(__name__)
TOKEN = os.environ["STUDENT_TOKEN"]


def imds(path):
    """Read instance metadata. Uses IMDSv2, so the token comes first."""
    req = urllib.request.Request(
        "http://169.254.169.254/latest/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
    )
    tok = urllib.request.urlopen(req, timeout=2).read().decode()
    req = urllib.request.Request(
        f"http://169.254.169.254/latest/meta-data/{path}",
        headers={"X-aws-ec2-metadata-token": tok},
    )
    return urllib.request.urlopen(req, timeout=2).read().decode()


INSTANCE_ID = imds("instance-id")
AZ = imds("placement/availability-zone")


def db():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=3,
    )


@app.get("/health")
def health():
    return "ok", 200


@app.get("/whoami")
def whoami():
    return jsonify(instance_id=INSTANCE_ID, az=AZ, student=TOKEN)


@app.get("/orders")
def orders():
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT count(*) AS n FROM orders")
            total = cur.fetchone()["n"]
            cur.execute(
                "SELECT id, customer, amount_cents, status "
                "FROM orders ORDER BY id LIMIT 10"
            )
            sample = cur.fetchall()
    return jsonify(served_by=INSTANCE_ID, total=total, sample=sample)
