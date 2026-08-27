
# On-Call & Incident Response

The engineering team runs a follow-the-sun on-call rotation across three
timezones (PST, GMT, SGT). Each shift covers one business day.

## Paging

- P0 incidents (revenue loss, data loss, or complete outage) page the
  on-call engineer immediately via PagerDuty.
- P1 incidents (partial outage, degraded performance) go to the on-call
  Slack channel and page after 15 minutes if unacknowledged.
- P2 and below stay in Slack for triage during business hours.

## Escalation

If the primary on-call doesn't ack within 5 minutes for P0 or 15
minutes for P1, PagerDuty escalates to the secondary. If secondary
doesn't ack within another 5 minutes, escalation goes to the engineering
manager for that service's team.

## Post-incident

Every P0 and P1 requires a written postmortem within 5 business days.
Postmortems are blameless. They live in the knowledge base under
incidents/.
