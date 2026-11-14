"""Money helpers: integer minor units, parsed with Decimal, never float."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# Prototype assumes 2-decimal currencies; 0- and 3-decimal currencies are
# listed under "considered, not built" (TDD §9).
_MINOR_UNITS_PER_MAJOR = Decimal(100)


def to_minor_units(amount: str | Decimal, quantity: int = 1) -> int:
    """Convert a decimal amount string such as ``"2.50"`` to cents."""
    value = Decimal(str(amount)) * quantity * _MINOR_UNITS_PER_MAJOR
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_minor_units(cents: int | None, currency: str | None) -> str:
    if cents is None:
        return "-"
    major = Decimal(cents) / _MINOR_UNITS_PER_MAJOR
    return f"{major:.2f} {currency or ''}".strip()
