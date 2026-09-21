"""ODC observability: logging + simple counters.

Keep it small. Rich for console, JSON for files. Counters for quick
metrics. No Prometheus, no OTEL — overkill for a v4 you can install
and run in two minutes.
"""
from odc.observability.logs import get_logger, log_event, setup_logging

__all__ = ["setup_logging", "get_logger", "log_event"]
