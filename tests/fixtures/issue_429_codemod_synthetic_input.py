"""Synthetic input file for the codemod idempotency test.

Contains a representative cross-section of the eager-logging patterns the
issue #429 codemod must rewrite. The idempotency test copies this file to
a tmp directory and runs the codemod twice; the second pass must produce
a zero-byte diff regardless of whether the first pass made any changes.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)  # A module logger keeps the record source readable.


def _emit_plain_fstring(value: str) -> None:
    """Plain f-string substitution (G004 base case)."""
    logger.info(f"x={value}")


def _emit_format_spec_2f(rate: float) -> None:
    """f-string with `.2f` numeric format spec."""
    logger.debug(f"rate={rate:.2f}")


def _emit_repr_conversion(item: object) -> None:
    """f-string using `!r` repr conversion."""
    logger.warning(f"item={item!r}")


def _emit_g003_concat(label: str, value: int) -> None:
    """String concatenation in logging call (G003)."""
    logger.info("label=" + label + " value=" + str(value))


def _emit_g201_in_except() -> None:
    """`logging.error(..., exc_info=True)` inside an except (G201)."""
    try:
        raise ValueError("synthetic")
    except ValueError as exc:
        logger.error(f"caught: {exc}", exc_info=True)


def _emit_already_lazy(name: str) -> None:
    """Already-lazy form; codemod must leave this untouched."""
    logger.info("name=%s", name)
