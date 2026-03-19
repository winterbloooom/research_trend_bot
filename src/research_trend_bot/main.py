"""Pipeline orchestrator: config → fetch → score → analyze → build → send."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from google import genai

from research_trend_bot.analyzer import analyze_papers
from research_trend_bot.config import get_gemini_api_key, get_github_token, get_smtp_password, load_config
from research_trend_bot.email_builder import build_email
from research_trend_bot.feedback import (
    build_feedback_urls,
    format_feedback_context,
    load_feedback_summary,
    load_recent_feedback,
)
from research_trend_bot.fetcher import fetch_papers
from research_trend_bot.models import DigestReport
from research_trend_bot.scorer import score_papers
from research_trend_bot.sender import send_email

logger = logging.getLogger(__name__)


def run(config_path: str) -> None:
    """Run the full digest pipeline."""
    # ── Load config ─────────────────────────────────────
    logger.info("Loading config from %s", config_path)
    config = load_config(config_path)
    api_key = get_gemini_api_key()
    smtp_password = get_smtp_password()

    client = genai.Client(api_key=api_key)

    # ── Load feedback (optional) ────────────────────────
    feedback_context = ""
    if config.feedback.enabled:
        token = get_github_token(config.feedback.github_token_env)
        if token:
            logger.info("=== Loading user feedback ===")
            feedback = load_recent_feedback(config, token)
            summary = load_feedback_summary()
            feedback_context = format_feedback_context(feedback, summary)
            if feedback_context:
                logger.info("Feedback context loaded (%d chars)", len(feedback_context))
        else:
            logger.warning("Feedback enabled but GitHub token not set; skipping")

    # ── Stage 0: Fetch papers ──────────────────────────
    logger.info("=== Stage 0: Fetching papers from arxiv ===")
    papers = fetch_papers(config)
    if not papers:
        logger.info("No papers found. Skipping email.")
        return

    # ── Stage 1: Score abstracts ───────────────────────
    logger.info("=== Stage 1: Scoring %d abstracts ===", len(papers))
    scored = score_papers(client, config, papers, feedback_context=feedback_context)
    if not scored:
        logger.info("No papers above relevance threshold. Skipping email.")
        return

    # ── Stage 2: Analyze full papers ───────────────────
    logger.info("=== Stage 2: Analyzing %d papers ===", len(scored))
    analyzed = analyze_papers(client, config, scored, feedback_context=feedback_context)
    if not analyzed:
        logger.info("No papers successfully analyzed. Skipping email.")
        return

    # ── Build email ────────────────────────────────────
    logger.info("=== Building email digest ===")
    report = DigestReport(
        generated_at=datetime.now(timezone.utc),
        research_interests=config.research_interests,
        total_fetched=len(papers),
        total_scored=len(scored),
        papers=analyzed,
    )

    # Build per-paper feedback URLs if enabled
    feedback_urls: dict[str, dict[str, str]] | None = None
    if config.feedback.enabled and config.feedback.github_repo:
        interest_names = [i.name for i in config.research_interests]
        feedback_urls = {}
        for item in analyzed:
            feedback_urls[item.paper.arxiv_id] = build_feedback_urls(
                config.feedback.github_repo, item, interest_names=interest_names
            )

    html_body, plain_body = build_email(report, feedback_urls=feedback_urls)
    subject = f"Research Digest - {report.generated_at.strftime('%Y-%m-%d')} ({len(analyzed)} papers)"

    # ── Send email ─────────────────────────────────────
    logger.info("=== Sending email ===")
    send_email(config, smtp_password, subject, html_body, plain_body)

    logger.info("Pipeline complete: %d papers delivered", len(analyzed))


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if len(sys.argv) < 2:
        config_path = "config.yaml"
    else:
        config_path = sys.argv[1]

    if not Path(config_path).exists():
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)

    try:
        run(config_path)
    except Exception:
        logger.exception("Pipeline failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
