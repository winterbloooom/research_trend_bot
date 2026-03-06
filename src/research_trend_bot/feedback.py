"""GitHub Issue-based feedback: load, format, summarize, and build URLs."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from google import genai
from google.genai import types

from research_trend_bot.models import AppConfig, FeedbackEntry
from research_trend_bot.prompts.feedback_summary import (
    SYSTEM_PROMPT,
    build_summary_prompt,
)

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
FEEDBACK_SUMMARY_PATH = Path("feedback_summary.json")


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }


def _parse_issue_body(body: str) -> dict[str, str]:
    """Parse structured fields from issue body.

    Supports two formats:
      - Issue Form output:  ### Label\n\nValue\n
      - Legacy body:        **Key**: value
    """
    fields: dict[str, str] = {}

    # Try Issue Form format first: ### Label\n\nValue
    form_matches = list(
        re.finditer(r"### (\w[\w\s]*?)\n\n(.+?)(?=\n###|\Z)", body, re.DOTALL)
    )
    if form_matches:
        for match in form_matches:
            key = match.group(1).strip().lower().replace(" ", "_")
            fields[key] = match.group(2).strip()
        return fields

    # Fallback: legacy **Key**: value format
    for match in re.finditer(
        r"\*\*(\w[\w\s]*?)\*\*:\s*(.+?)(?=\n\*\*|\Z)", body, re.DOTALL
    ):
        key = match.group(1).strip().lower().replace(" ", "_")
        fields[key] = match.group(2).strip()
    return fields


def load_recent_feedback(
    config: AppConfig, token: str, days: int = 7
) -> list[FeedbackEntry]:
    """Load recent feedback issues from GitHub."""
    repo = config.feedback.github_repo
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    url = f"{GITHUB_API}/repos/{repo}/issues"
    params = {
        "labels": "feedback",
        "state": "open",
        "since": since,
        "per_page": 100,
    }

    try:
        with httpx.Client(timeout=30) as http:
            resp = http.get(url, headers=_github_headers(token), params=params)
            resp.raise_for_status()
            issues = resp.json()
    except httpx.HTTPError:
        logger.exception("Failed to fetch feedback issues from GitHub")
        return []

    entries: list[FeedbackEntry] = []
    for issue in issues:
        labels = [l["name"] for l in issue.get("labels", [])]
        body = issue.get("body", "") or ""
        fields = _parse_issue_body(body)

        rating = "positive" if "positive" in labels else "negative"
        entry = FeedbackEntry(
            rating=rating,
            paper_title=fields.get("paper", issue.get("title", "")),
            bot_score=fields.get("bot_score", ""),
            reason=fields.get("reason", ""),
            issue_number=issue.get("number", 0),
            created_at=issue.get("created_at", ""),
        )
        entries.append(entry)

    logger.info("Loaded %d feedback entries from GitHub", len(entries))
    return entries


def load_feedback_summary() -> dict | None:
    """Load feedback summary from local JSON file."""
    if not FEEDBACK_SUMMARY_PATH.exists():
        return None
    try:
        data = json.loads(FEEDBACK_SUMMARY_PATH.read_text())
        return data if data else None
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to load feedback summary")
        return None


def format_feedback_context(
    feedback: list[FeedbackEntry],
    summary: dict | None = None,
) -> str:
    """Format feedback into a prompt-injectable context string.

    Returns empty string if no feedback data.
    """
    if not feedback and not summary:
        return ""

    parts: list[str] = ["## User Feedback Context"]

    if summary and summary.get("summary"):
        parts.append("### Summary")
        parts.append(summary["summary"])

    if feedback:
        parts.append("### Recent (7 days)")
        for entry in feedback:
            emoji = "\U0001f44d" if entry.rating == "positive" else "\U0001f44e"
            score_info = f" ({entry.bot_score})" if entry.bot_score else ""
            reason_info = f" | {entry.reason}" if entry.reason else ""
            parts.append(
                f"- {emoji} \"{entry.paper_title}\"{score_info}{reason_info}"
            )

    return "\n".join(parts)


def build_feedback_urls(
    github_repo: str, item: object
) -> dict[str, str]:
    """Build GitHub Issue creation URLs for thumbs up/down feedback.

    Args:
        github_repo: "owner/repo" string
        item: AnalyzedPaper with .paper.arxiv_id, .paper.title, .relevance.score
    """
    paper = item.paper  # type: ignore[attr-defined]
    relevance = item.relevance  # type: ignore[attr-defined]

    arxiv_id = paper.arxiv_id
    score = relevance.score

    urls: dict[str, str] = {}
    for rating in ("positive", "negative"):
        template = f"feedback_{rating}.yml"
        issue_title = quote(f"[{rating}] {paper.title}")
        paper_encoded = quote(paper.title)
        arxiv_encoded = quote(arxiv_id)
        score_encoded = quote(str(score))

        urls[rating] = (
            f"https://github.com/{github_repo}/issues/new"
            f"?template={template}"
            f"&title={issue_title}"
            f"&paper={paper_encoded}"
            f"&arxiv_id={arxiv_encoded}"
            f"&bot_score={score_encoded}"
        )

    return urls


def summarize_and_cleanup(
    config: AppConfig,
    client: genai.Client,
    token: str,
) -> None:
    """Summarize all open feedback, save summary, and close old issues."""
    repo = config.feedback.github_repo

    # Collect all open feedback issues
    all_issues: list[dict] = []
    page = 1
    while True:
        url = f"{GITHUB_API}/repos/{repo}/issues"
        params = {
            "labels": "feedback",
            "state": "open",
            "per_page": 100,
            "page": page,
        }
        with httpx.Client(timeout=30) as http:
            resp = http.get(url, headers=_github_headers(token), params=params)
            resp.raise_for_status()
            issues = resp.json()

        if not issues:
            break
        all_issues.extend(issues)
        page += 1

    if not all_issues:
        logger.info("No open feedback issues to summarize")
        return

    # Parse all entries for summarization
    entries: list[FeedbackEntry] = []
    for issue in all_issues:
        labels = [l["name"] for l in issue.get("labels", [])]
        body = issue.get("body", "") or ""
        fields = _parse_issue_body(body)
        rating = "positive" if "positive" in labels else "negative"
        entries.append(
            FeedbackEntry(
                rating=rating,
                paper_title=fields.get("paper", issue.get("title", "")),
                bot_score=fields.get("bot_score", ""),
                reason=fields.get("reason", ""),
                issue_number=issue.get("number", 0),
                created_at=issue.get("created_at", ""),
            )
        )

    # Build summarization prompt
    feedback_text = "\n".join(
        f"- [{e.rating}] \"{e.paper_title}\" (score={e.bot_score}) {e.reason}"
        for e in entries
    )
    prompt = build_summary_prompt(feedback_text, language=config.language)

    response = client.models.generate_content(
        model=config.llm.analysis_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.3,
        ),
    )

    summary_text = response.text.strip()
    summary_data = {
        "summary": summary_text,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_entries": len(entries),
    }
    FEEDBACK_SUMMARY_PATH.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2))
    logger.info("Feedback summary saved (%d entries)", len(entries))

    # Close issues older than 30 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    closed = 0
    with httpx.Client(timeout=30) as http:
        for issue in all_issues:
            created = datetime.fromisoformat(
                issue["created_at"].replace("Z", "+00:00")
            )
            if created < cutoff:
                patch_url = f"{GITHUB_API}/repos/{repo}/issues/{issue['number']}"
                resp = http.patch(
                    patch_url,
                    headers=_github_headers(token),
                    json={"state": "closed"},
                )
                if resp.is_success:
                    closed += 1

    logger.info("Closed %d feedback issues older than 30 days", closed)
