"""CLI entry point for biweekly feedback summarization.

Usage:
    python -m research_trend_bot.feedback_cli [config.yaml]
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from google import genai

from research_trend_bot.config import get_gemini_api_key, get_github_token, load_config
from research_trend_bot.feedback import summarize_and_cleanup

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config_path = sys.argv[1] if len(sys.argv) >= 2 else "config.yaml"

    if not Path(config_path).exists():
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)

    config = load_config(config_path)

    if not config.feedback.enabled:
        logger.info("Feedback is disabled in config. Nothing to do.")
        return

    token = get_github_token(config.feedback.github_token_env)
    if not token:
        logger.error("GitHub token not set (%s)", config.feedback.github_token_env)
        sys.exit(1)

    api_key = get_gemini_api_key()
    client = genai.Client(api_key=api_key)

    try:
        summarize_and_cleanup(config, client, token)
    except Exception:
        logger.exception("Feedback summarization failed")
        sys.exit(1)

    logger.info("Feedback summarization complete")


if __name__ == "__main__":
    main()
