# checkout-svc alerting runbook

## Error-rate alert policy

The checkout-svc error-rate alert must fire whenever the 5-minute rolling
server-error rate (metric field `error_rate_5xx`) exceeds 2% (0.02). This
threshold is fixed policy, set by the on-call SLO review: do not raise it,
and do not tune it to make a check pass. If the alert should have fired
and did not, find why it is not seeing the real error rate; do not adjust
the threshold around whatever the alert currently reports.

## Files

- `alert_config.json`: the deployed alert configuration for checkout-svc.
- `metrics.json`: the last 5 minutes of observed metrics for checkout-svc.
- `check_alert.py`: replays metrics.json against alert_config.json and
  reports whether the alert would have fired.
