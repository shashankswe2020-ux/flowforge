#!/usr/bin/env python3
"""FlowForge CLI — run the full pipeline end-to-end.

Usage:
    python -m scripts.run_pipeline "Build a web scraper for Hacker News"
    python -m scripts.run_pipeline "Build a REST API" --repo my-api
    python -m scripts.run_pipeline "Build a CLI tool" --skip-github
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from langchain_openai import ChatOpenAI

from src.runner.pipeline import PipelineRunner


def get_github_token() -> str:
    """Get GitHub token from gh CLI."""
    result = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def create_llm() -> ChatOpenAI:
    """Create LLM using GitHub Models API (OpenAI-compatible)."""
    token = get_github_token()
    return ChatOpenAI(
        model="gpt-4o-mini",
        api_key=token,
        base_url="https://models.inference.ai.azure.com",
        temperature=0.0,
        max_tokens=4096,
    )


class LLMWrapper:
    """Wraps ChatOpenAI to match LLMProtocol."""

    def __init__(self, llm: ChatOpenAI) -> None:
        self._llm = llm

    def invoke(self, prompt: str):
        return self._llm.invoke(prompt)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FlowForge — AI-powered code generation pipeline"
    )
    parser.add_argument("prompt", help="Natural language description of what to build")
    parser.add_argument("--repo", help="GitHub repo name (creates if missing)")
    parser.add_argument(
        "--skip-github", action="store_true", help="Generate files locally only"
    )
    parser.add_argument(
        "--output-dir", type=Path, help="Output directory (default: temp dir)"
    )
    args = parser.parse_args()

    print("=" * 70)
    print("FlowForge — AI-Powered Code Generation Pipeline")
    print("=" * 70)
    print(f"\nPrompt: {args.prompt}")
    print()

    # Connect to LLM
    print("Connecting to GitHub Models API (gpt-4o-mini)...")
    raw_llm = create_llm()
    llm = LLMWrapper(raw_llm)
    print(f"  ✓ Connected: {raw_llm.model_name} @ {raw_llm.openai_api_base}")

    # Run pipeline
    runner = PipelineRunner(llm, output_dir=args.output_dir)
    result = runner.run(
        args.prompt,
        repo_name=args.repo,
        skip_github=args.skip_github,
    )

    # Summary
    print("\n" + "=" * 70)
    print("PIPELINE COMPLETE")
    print("=" * 70)
    print(result.summary())

    if result.succeeded:
        print("\n✅ Success!")
    else:
        print(f"\n❌ Pipeline ended with status: {result.state.run_status}")
        sys.exit(1)


if __name__ == "__main__":
    main()
