"""CLI: propose an interests.yaml update from feedback and open a PR + email.

Usage:
    python -m research_trend_bot.interests_cli [config.yaml]

Runs after the weekly feedback summary. Opens a reviewable pull request when
the LLM proposes a change; never edits the main branch directly. Sending the
notification email and opening the PR both require their usual secrets
(SMTP_PASSWORD, a GITHUB_TOKEN with pull-requests:write).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from google import genai

from research_trend_bot.config import (
    get_gemini_api_key,
    get_github_token,
    get_smtp_password,
    load_config,
)
from research_trend_bot.interests_tuner import (
    INTERESTS_PATH,
    build_proposal_email,
    interests_changed,
    open_interests_pr,
    propose_interests_update,
    render_interests_yaml,
    summarize_changes,
    unified_diff,
)
from research_trend_bot.sender import send_email

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

    client = genai.Client(api_key=get_gemini_api_key())

    proposal = propose_interests_update(config, client, token)
    if proposal is None:
        logger.info("No proposal produced; nothing to do.")
        return
    if not proposal.changed or not interests_changed(
        config.research_interests, proposal.research_interests
    ):
        logger.info("Interests unchanged; no PR needed.")
        return

    current_yaml = INTERESTS_PATH.read_text() if INTERESTS_PATH.exists() else ""
    new_yaml = render_interests_yaml(INTERESTS_PATH, proposal.research_interests)
    change_summary = summarize_changes(
        config.research_interests, proposal.research_interests
    )
    diff_text = unified_diff(current_yaml, new_yaml)

    logger.info("Proposed interests change:\n%s", change_summary)

    pr_body = f"{proposal.rationale}\n\n### Changes\n{change_summary}"
    pr_url = open_interests_pr(
        config.feedback.github_repo, token, new_yaml, pr_body
    )
    if pr_url:
        logger.info("Opened interests-update PR: %s", pr_url)

    # Notify by email (best-effort — a failed send shouldn't fail the job).
    try:
        smtp_password = get_smtp_password()
        email_body = f"{proposal.rationale}\n\n=== Changes ===\n{change_summary}"
        html_body, plain_body = build_proposal_email(
            email_body, diff_text, pr_url
        )
        send_email(
            config,
            smtp_password,
            "Research Bot: proposed interests update",
            html_body,
            plain_body,
        )
    except Exception:
        logger.exception("Failed to send interests-proposal email")

    logger.info("Interests tuning complete")


if __name__ == "__main__":
    main()
