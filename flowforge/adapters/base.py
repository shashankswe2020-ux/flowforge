"""Base adapter contract and shared infrastructure."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.adapters.schemas import CanonicalRequest, CanonicalResponse


@dataclass
class IntegrationError(Exception):
    """Typed error from adapter failures — preserves context without corrupting state."""

    adapter_id: str
    original_error: Exception
    message: str

    def __str__(self) -> str:
        return f"[{self.adapter_id}] {self.message}"


class RateLimitExceededError(Exception):
    """Raised when adapter exceeds its rate limit policy."""

    def __init__(self, *, adapter_id: str, limit: int) -> None:
        self.adapter_id = adapter_id
        self.limit = limit
        super().__init__(
            f"Rate limit exceeded for adapter '{adapter_id}': {limit} requests/minute",
        )


class RateLimitPolicy:
    """Simple sliding-window rate limiter per adapter.

    Tracks request count within the current window. Call reset()
    to start a new window (e.g., after 60 seconds elapse).
    """

    def __init__(self, *, max_requests_per_minute: int) -> None:
        self._max = max_requests_per_minute
        self._count = 0

    @property
    def max_requests_per_minute(self) -> int:
        """Maximum allowed requests per minute."""
        return self._max

    @property
    def current_count(self) -> int:
        """Number of requests recorded in current window."""
        return self._count

    def record_request(self) -> None:
        """Record a request in the current window."""
        self._count += 1

    def is_allowed(self) -> bool:
        """Check if another request is allowed within the limit."""
        return self._count < self._max

    def check_or_raise(self, adapter_id: str = "unknown") -> None:
        """Raise RateLimitExceededError if limit is exceeded."""
        if not self.is_allowed():
            raise RateLimitExceededError(adapter_id=adapter_id, limit=self._max)

    def reset(self) -> None:
        """Reset the window counter."""
        self._count = 0


class AdapterBase(ABC):
    """Abstract base class for assistant adapters.

    All adapters must implement:
    - normalize_request: raw input → CanonicalRequest
    - normalize_response: graph state → CanonicalResponse
    - map_error: exception → IntegrationError

    Required attributes:
    - adapter_id: unique identifier for this adapter
    - auth_mode: authentication mode (token, oauth, api_key)
    """

    adapter_id: str
    auth_mode: str

    @abstractmethod
    def normalize_request(self, raw_input: dict[str, object]) -> CanonicalRequest:
        """Normalize provider-specific input to canonical request schema."""

    @abstractmethod
    def normalize_response(self, state: dict[str, object]) -> CanonicalResponse:
        """Normalize graph output state to canonical response schema."""

    @abstractmethod
    def map_error(self, error: Exception) -> IntegrationError:
        """Map any exception to a typed IntegrationError."""
