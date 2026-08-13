# orders-api capstone

A multi-VPC AWS deployment of a small Flask orders API, assessed on routing
rather than on the application. The infrastructure will be declared in Pulumi
(Python); the app exists to give the routing something to carry.

**Status: phase 0 complete — local skeleton only. No AWS resources exist yet.**

## What is here

| Path | What it is |
| --- | --- |
| [app/app.py](app/app.py) | Flask app: `/health`, `/whoami`, `/orders`. |
| [app/requirements.txt](app/requirements.txt) | Pinned Python dependencies. |
| [db/schema.sql](db/schema.sql) | Postgres schema, seed data, application role and grants. |
| [collect-evidence.sh](collect-evidence.sh) | Supplied by the assessment. Not modified. |
| [.gitignore](.gitignore) | Excludes key material and generated env files. |
| [routing.md](routing.md) | Empty — the routing write-up, filled in phase 8. |
| [traceability.md](traceability.md) | Empty — requirement → resource → evidence map, phase 8. |
| [ai-log.md](ai-log.md) | Empty — record of AI assistance. |
| [teardown.txt](teardown.txt) | Empty — teardown procedure, phase 9. |

## Changes made to the supplied files

Three defects were fixed in phase 0, recorded here because they are not
self-evident from reading the diff.

**Connection leak in `/orders`** — [app/app.py:50](app/app.py#L50). The original
used `with psycopg2.connect(...) as conn`, whose context manager ends the
*transaction* but never closes the connection. Under gunicorn that leaks one
connection per request until Postgres refuses new ones — and `/orders` is an
endpoint the evidence collector calls. Now wrapped in `contextlib.closing()`.

**Schema was not re-appliable and granted nothing** —
[db/schema.sql](db/schema.sql). `CREATE TABLE orders` failed on a second run, so
a partly-applied schema blocked the next attempt on a rebuilt DB host. It also
created no role for `DB_USER`, while `app.py` connects as a non-superuser and so
needs explicit `SELECT` on both the table and the `orders_id_seq` sequence —
without which `/orders` returns `permission denied`. Now uses
`CREATE TABLE IF NOT EXISTS`, a guarded seed, and `\gexec` for idempotent role
creation. Takes three `psql -v` variables so the password stays out of git.

**Unpinned dependencies** — [app/requirements.txt](app/requirements.txt). The
lab VM is wiped on every restart, so an unpinned `flask`/`psycopg2-binary` means
a build that worked yesterday can break today. All three are pinned.

## Phases

The lab VM is wiped on every restart, so the work is split into phases that each
end at a committed, reproducible state.

| # | Phase | Status |
| --- | --- | --- |
| 0 | Local skeleton | **done** |
| 1 | Pulumi project + recovery path | next |
| 2 | Network — VPCs, subnets, NAT, TGW, route tables | |
| 3 | Compute — bastion, edge, app-a, app-b, db, security groups | |
| 4 | App deployment under systemd | |
| 5 | Database — Postgres install, schema, isolation | |
| 6 | Load balancing — nginx round-robin and failover | |
| 7 | Automation — S3 dump bucket, Lambda, schedule | |
| 8 | Documentation — `routing.md`, `traceability.md`, `ai-log.md` | |
| 9 | Evidence collection and teardown | |

## Not yet done

- No git repository. `git init` and the GitHub remote come before phase 1.
- No Pulumi project. Phase 1 creates `iac/` with
  `pulumi new aws-python --dir iac`.
- The address plan is undecided pending the assignment brief's constraints on
  region, CIDRs and VPC count.
