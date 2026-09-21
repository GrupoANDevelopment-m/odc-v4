"""Shared money helpers for the export layer."""


def to_cents(amount):
    """Convert a decimal amount to integer cents, correctly rounded."""
    return int(round(amount * 100))


def fmt_cents(cents):
    return f"{cents}"
