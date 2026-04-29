# Quarterly Threshold and Model Review Runbook

## Purpose

This runbook defines the quarterly governance board process for biometric performance, fairness, and lifecycle controls.

## Scope

- Recognition threshold policy (strict, relaxed, margin)
- Fairness and disparity monitoring
- False match and false non-match trends
- Template aging and auto-refresh policy
- Model update decisions and rollback readiness

## Inputs Required Before Meeting

1. Latest fairness audit report at `backend/data/audits/fairness_audit_latest.json`
2. Last 90 days of false match / false non-match trend snapshots
3. Template lifecycle metrics:
- Active template age histogram
- Auto-refresh volume and rollback count
4. Data retention compliance status and right-to-deletion ticket outcomes
5. Camera drift alerts and remediation outcomes

## Quarterly Review Agenda

1. Fairness trend review
- Compare disparity ratios by department and enrollment year versus last quarter.
- Flag cohorts where recall or precision disparity exceeds policy tolerance.

2. Error-rate trend review
- Review FMR and FNMR trend lines for overall and high-traffic cameras.
- Identify whether threshold changes caused regressions.

3. Template aging review
- Evaluate share of active templates older than 180 days.
- Review auto-refresh acceptance rate and rollback rate.

4. Threshold and model decision
- Decide whether to adjust confidence thresholds, fusion weights, or model routing.
- For model changes, require controlled A/B evidence before production rollout.

5. Action tracking
- Assign owners and due dates for remediation items.
- Log accepted risks with expiry date and re-review trigger.

## Decision Criteria

- Do not lower `auto_refresh_min_confidence` below `0.98`.
- Do not approve changes that increase false-match risk without compensating controls.
- Require documented rationale and rollback path for every threshold/model adjustment.

## Outputs

1. Governance decision record with:
- approved changes
- rejected alternatives
- rationale and expected impact

2. Implementation tasks for:
- threshold updates
- model upgrades
- drift remediation
- fairness mitigation actions

3. Follow-up review date and success metrics.

## Escalation Triggers

Escalate to security/compliance owner when any of the following is true:

- persistent disparity ratio regression across two consecutive audits
- sudden FMR spike beyond tolerated operational envelope
- repeated auto-refresh rollback clusters on the same cohort/camera
- unprocessed right-to-deletion requests beyond SLA
