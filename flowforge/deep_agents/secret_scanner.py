"""Diff-based secret scanner for the implementer Deep Agent (T9, spec §13.14).

Scans only the *added* lines of a unified-diff and reports findings
classified by ``SecretSeverity``:

* :attr:`SecretSeverity.HIGH` — strict regex match for a known token
  shape (AWS access key, GitHub PAT, OpenAI key, Slack token, PEM
  private-key header). High-confidence findings block the run.
* :attr:`SecretSeverity.MEDIUM` — long high-entropy quoted string with
  no known shape. Advisory.

Diff lines starting with ``---``/``+++`` (file headers) are skipped so
they cannot trip the regex on a path that happens to look like a key.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "SecretFinding",
    "SecretSeverity",
    "has_blocking_secret",
    "scan_diff",
]


class SecretSeverity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """One detected secret in an added diff line."""

    pattern_name: str
    severity: SecretSeverity
    line: int
    snippet: str


# Regex catalogue. Anchors and character classes are deliberately strict
# so we minimise false positives — plain code should never match.
_HIGH_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_pat_classic", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    # Fine-grained GitHub PAT (`github_pat_<22>_<59>`).
    ("github_pat_fine", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b")),
    # Server-to-server / OAuth / refresh / user-server tokens.
    ("github_token_other", re.compile(r"\bgh[osur]_[A-Za-z0-9]{36,}\b")),
    # OpenAI: classic `sk-…`, project keys `sk-proj-…`, service `sk-svcacct-…`.
    (
        "openai_key",
        re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}\b"),
    ),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe_live_secret", re.compile(r"\bsk_live_[A-Za-z0-9]{24,}\b")),
    (
        "private_key_header",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |)PRIVATE KEY-----"),
    ),
)

# Entropy heuristic: long quoted blob that doesn't match a known shape.
_QUOTED_BLOB = re.compile(r"['\"]([A-Za-z0-9+/=_\-]{32,})['\"]")
_ENTROPY_THRESHOLD: Final[float] = 4.0  # bits per char


def _shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    length = len(text)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def scan_diff(diff_text: str) -> list[SecretFinding]:
    """Return findings for every added line in ``diff_text``.

    Only ``+``-prefixed lines are scanned; ``+++`` file headers are
    ignored. Each line is checked against every high-confidence regex
    first; if none match, the entropy heuristic runs on quoted blobs.
    """
    findings: list[SecretFinding] = []
    for lineno, raw in enumerate(diff_text.splitlines(), start=1):
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        line = raw[1:]
        matched_high = False
        for name, pattern in _HIGH_PATTERNS:
            if pattern.search(line):
                findings.append(
                    SecretFinding(
                        pattern_name=name,
                        severity=SecretSeverity.HIGH,
                        line=lineno,
                        snippet=line.strip()[:120],
                    ),
                )
                matched_high = True
        if matched_high:
            continue
        for blob_match in _QUOTED_BLOB.finditer(line):
            blob = blob_match.group(1)
            if _shannon_entropy(blob) >= _ENTROPY_THRESHOLD:
                findings.append(
                    SecretFinding(
                        pattern_name="entropy",
                        severity=SecretSeverity.MEDIUM,
                        line=lineno,
                        snippet=blob[:60],
                    ),
                )
    return findings


def has_blocking_secret(findings: list[SecretFinding]) -> bool:
    """Return ``True`` if any finding is HIGH severity."""
    return any(f.severity == SecretSeverity.HIGH for f in findings)
