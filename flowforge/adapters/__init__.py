"""Adapter package — assistant integration layer."""

from __future__ import annotations

from src.adapters.base import AdapterBase, IntegrationError, RateLimitPolicy
from src.adapters.schemas import CanonicalRequest, CanonicalResponse

__all__ = [
    "AdapterBase",
    "CanonicalRequest",
    "CanonicalResponse",
    "IntegrationError",
    "RateLimitPolicy",
]
