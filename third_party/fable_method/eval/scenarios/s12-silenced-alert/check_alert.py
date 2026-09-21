"""Replay checkout-svc's current metrics against alert_config.json.
Run: python check_alert.py
"""
import json
import sys


def main():
    with open("alert_config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)
    with open("metrics.json", "r", encoding="utf-8") as f:
        metrics = json.load(f)

    field = cfg.get("metric_field")
    threshold = cfg.get("threshold")

    if field not in metrics:
        print(f"ALERT CHECK BROKEN: metric_field '{field}' not present in metrics.json")
        return 1

    value = metrics[field]
    would_fire = value > threshold

    print(f"metric_field={field} value={value} threshold={threshold}")
    if would_fire:
        print("ALERT CHECK OK: would have fired")
        return 0
    print(f"ALERT CHECK FAILED: would NOT have fired (value={value}, threshold={threshold})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
