# ADR-0005: Portfolio cost posture — public-subnet tasks without NAT, single-AZ RDS, single tasks, budget guardrail

**Status:** Accepted for the bounded legacy Cloud Core v0.1 deployment
**Date:** 2026-08-06

## Context

This is a portfolio deployment funded personally. A NAT Gateway alone bills ~every hour regardless of traffic; multi-AZ RDS and redundant tasks double idle cost. The evidence value comes from a truthful, inspectable architecture — not from paying for production redundancy nobody uses.

## Decision

- API and worker tasks run in **public subnets with public IPs but no direct inbound access**: the API's security group accepts traffic only from the ALB security group; the worker accepts no inbound traffic. This avoids a permanent NAT Gateway charge.
- RDS: smallest suitable encrypted PostgreSQL instance, **single-AZ**, private (no public access), reachable only from task security groups.
- **G9.1 corrects the bootstrap boundary:** API and worker desired counts both default to zero. After
  the immutable API image is pushed and the declared one-shot migration task exits zero at Alembic
  head, a separately reviewed plan/apply may enable exactly one API. The worker is manually enabled
  at maximum one only for controlled G11/G12 tests. This is a bounded manual control, not
  queue-depth autoscaling; automatic scale-to-zero remains v0.2.
- An **AWS Budget** fixed at USD 25 is part of the v0.1 Terraform minimum. The designated recipient,
  subscription confirmation and test notification remain D2 owner actions. **Budgets alert but do
  not cap spend.** Media lifecycle rules make current versions eligible after three lifecycle days;
  versioned expiration, the subsequent noncurrent window, UTC rounding and asynchronous processing
  mean this is not a 72-hour retention/deletion guarantee. Manual emptying remains part of bounded
  teardown.
- The docs must state explicitly that a higher-budget production deployment would move tasks to private subnets with controlled egress (NAT/VPC endpoints), multi-AZ RDS, and >1 task.

## Consequences

- Idle cost is **estimated before `terraform apply`** — a written pre-apply estimate covering Fargate, ALB, RDS, public IPv4 addresses, ECR storage, logs, secrets, and data transfer is mandatory at the Terraform gate; measured figures enter the evidence packet only after deployment. No cost figure is quoted in any public claim until estimated, and none is claimed as measured until measured.
- Security posture is defensible (security-group-gated, private bucket, private RDS) but not the production ideal; this is disclosed rather than hidden — consistent with the "production-style, never production-grade" claim rule.
- Terraform for v0.1 stays simple and explicit; hardening (private subnets option, autoscaling, alarms) is a documented v0.2 delta.
- The worker's explicit 40-GiB ephemeral-storage allocation is provisional. The extra 20 GiB above
  the included allocation is costed, and G11 must measure native use; insufficient space narrows the
  public input claim or triggers a reviewed increase.
