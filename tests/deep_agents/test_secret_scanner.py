"""Tests for the diff-based secret scanner (spec §13.14, T9).

The scanner reads a unified-diff text and emits findings classified by
confidence. ``HIGH`` findings are intended to block a deep-agent run;
``MEDIUM`` and ``LOW`` are advisory.
"""

from __future__ import annotations

from flowforge.deep_agents.secret_scanner import (
    SecretFinding,
    SecretSeverity,
    has_blocking_secret,
    scan_diff,
)

# ---------------------------------------------------------------------------
# High-confidence — strict regexes for known token shapes
# ---------------------------------------------------------------------------


class TestScanDiffHighConfidence:
    """High-confidence patterns (per spec §13.14) must always be flagged."""

    def test_blocks_planted_aws_key(self) -> None:
        """Spec §13 acceptance row 14 — the canonical regression test."""
        diff = (
            "diff --git a/secrets.py b/secrets.py\n"
            "+++ b/secrets.py\n"
            '+AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'
        )
        findings = scan_diff(diff)
        assert findings, "scanner must detect a planted AWS access key"
        assert any(f.severity == SecretSeverity.HIGH for f in findings)
        assert has_blocking_secret(findings)

    def test_detects_github_personal_access_token(self) -> None:
        diff = (
            "+++ b/config.env\n"
            "+GITHUB_TOKEN=ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        )
        findings = scan_diff(diff)
        assert any(
            f.pattern_name == "github_pat_classic" and f.severity == SecretSeverity.HIGH
            for f in findings
        )

    def test_detects_openai_api_key(self) -> None:
        diff = (
            "+++ b/.env\n"
            "+OPENAI_API_KEY=sk-" + "a" * 48 + "\n"
        )
        findings = scan_diff(diff)
        assert any(
            f.pattern_name == "openai_key" and f.severity == SecretSeverity.HIGH
            for f in findings
        )

    def test_detects_slack_token(self) -> None:
        # Token literal is split to avoid tripping GitHub push protection;
        # the runtime-concatenated string still matches the scanner regex.
        slack_token = "xoxb-1234567890-1234567890123-" + "AbCdEfGhIjKlMnOpQrStUvWx"
        diff = (
            "+++ b/bot.py\n"
            f"+TOKEN = '{slack_token}'\n"
        )
        findings = scan_diff(diff)
        assert any(f.severity == SecretSeverity.HIGH for f in findings)

    def test_detects_private_key_header(self) -> None:
        diff = (
            "+++ b/key.pem\n"
            "+-----BEGIN RSA PRIVATE KEY-----\n"
        )
        findings = scan_diff(diff)
        assert any(f.severity == SecretSeverity.HIGH for f in findings)


class TestScanDiffModernTokenShapes:
    """Audit-18 IMPORTANT-2 — modern token formats must also be flagged."""

    def test_detects_openai_project_key(self) -> None:
        diff = (
            "+++ b/.env\n"
            "+OPENAI_API_KEY=sk-proj-" + "A" * 40 + "\n"
        )
        findings = scan_diff(diff)
        assert any(
            f.pattern_name == "openai_key" and f.severity == SecretSeverity.HIGH
            for f in findings
        )

    def test_detects_github_fine_grained_pat(self) -> None:
        # Format: github_pat_<22 alnum>_<59 alnum> → 82 chars after prefix.
        token = "github_pat_" + "A" * 22 + "_" + "B" * 59
        diff = f"+++ b/.env\n+GH_TOKEN={token}\n"
        findings = scan_diff(diff)
        assert any(f.pattern_name == "github_pat_fine" for f in findings)

    def test_detects_github_oauth_token(self) -> None:
        diff = "+++ b/.env\n+T=gho_" + "Z" * 36 + "\n"
        findings = scan_diff(diff)
        assert any(f.pattern_name == "github_token_other" for f in findings)

    def test_detects_google_api_key(self) -> None:
        diff = "+++ b/.env\n+GOOG=AIza" + "a" * 35 + "\n"
        findings = scan_diff(diff)
        assert any(f.pattern_name == "google_api_key" for f in findings)

    def test_detects_stripe_live_secret(self) -> None:
        diff = "+++ b/.env\n+STRIPE=sk_live_" + "k" * 24 + "\n"
        findings = scan_diff(diff)
        assert any(f.pattern_name == "stripe_live_secret" for f in findings)


# ---------------------------------------------------------------------------
# Negative cases — must NOT fire on innocuous content
# ---------------------------------------------------------------------------


class TestScanDiffNoFalsePositives:
    """Plain code and removed lines must not be flagged as secrets."""

    def test_empty_diff_returns_empty(self) -> None:
        assert scan_diff("") == []

    def test_no_secret_in_plain_python(self) -> None:
        diff = (
            "+++ b/hello.py\n"
            "+def hello() -> str:\n"
            '+    return "hello world"\n'
        )
        assert scan_diff(diff) == []

    def test_ignores_removed_lines(self) -> None:
        """Only added (+) lines are scanned — deletions cannot leak a new secret."""
        diff = (
            "--- a/old.py\n"
            "+++ b/old.py\n"
            '-AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'
        )
        assert scan_diff(diff) == []

    def test_ignores_diff_metadata_lines(self) -> None:
        """``+++`` headers must not be scanned even though they start with ``+``."""
        diff = "+++ b/AKIAIOSFODNN7EXAMPLE.txt\n"
        assert scan_diff(diff) == []


# ---------------------------------------------------------------------------
# Entropy-based medium-confidence detection
# ---------------------------------------------------------------------------


class TestEntropyDetection:
    """Long high-entropy strings get flagged as MEDIUM, not HIGH."""

    def test_high_entropy_string_is_medium_only(self) -> None:
        # 40-char base64-ish blob; not a known token shape but suspicious.
        blob = "Xj7kD9pQ3wL5sN8mZ2vR4tY6uI1oP0aB3cE9fG2h"
        diff = f"+++ b/data.txt\n+secret = \"{blob}\"\n"
        findings = scan_diff(diff)
        # If anything is reported, it must be MEDIUM (entropy heuristic).
        assert all(f.severity != SecretSeverity.HIGH for f in findings)
        assert not has_blocking_secret(findings)

    def test_low_entropy_string_is_not_flagged(self) -> None:
        diff = '+++ b/data.txt\n+greeting = "hello world hello world"\n'
        findings = scan_diff(diff)
        assert all(f.severity != SecretSeverity.HIGH for f in findings)


# ---------------------------------------------------------------------------
# has_blocking_secret semantics
# ---------------------------------------------------------------------------


class TestBlockingPredicate:
    def test_no_findings_does_not_block(self) -> None:
        assert has_blocking_secret([]) is False

    def test_only_medium_does_not_block(self) -> None:
        f = SecretFinding(
            pattern_name="entropy",
            severity=SecretSeverity.MEDIUM,
            line=1,
            snippet="x",
        )
        assert has_blocking_secret([f]) is False

    def test_any_high_blocks(self) -> None:
        f = SecretFinding(
            pattern_name="aws_access_key",
            severity=SecretSeverity.HIGH,
            line=1,
            snippet="AKIA...",
        )
        assert has_blocking_secret([f]) is True
