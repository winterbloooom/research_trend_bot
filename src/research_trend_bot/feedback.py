"""GitHub Issue-based feedback: load, format, summarize, and build URLs."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import arxiv
import httpx
from google import genai
from google.genai import types

from research_trend_bot.models import AppConfig, FeedbackEntry, ReferencePaper
from research_trend_bot.prompts.feedback_summary import (
    SYSTEM_PROMPT,
    build_summary_prompt,
)

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
FEEDBACK_SUMMARY_PATH = Path("feedback_summary.json")

# Label applied by the reference-paper Issue Form template.
REFERENCE_LABEL = "reference-paper"
# First field label emitted in the reference Issue Form body. Used as a
# fallback detector so a missing label can't silently drop a submission.
_REFERENCE_BODY_MARKER = "### Paper links"
# Cap on reference papers injected into the prompt, to bound token usage.
MAX_REFERENCE_PAPERS = 20
# Matches modern arxiv IDs (e.g. 2401.12345) in URLs or bare text.
_ARXIV_ID_RE = re.compile(r"\b(\d{4}\.\d{4,5})(?:v\d+)?\b")


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
            interest=fields.get("interest", fields.get("matched_interest", "")),
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
            interest_info = f" [interest: {entry.interest}]" if entry.interest else ""
            parts.append(
                f"- {emoji} \"{entry.paper_title}\"{score_info}{reason_info}{interest_info}"
            )

    return "\n".join(parts)


def build_feedback_urls(
    github_repo: str, item: object, interest_names: list[str] | None = None
) -> dict[str, str]:
    """Build GitHub Issue creation URLs for thumbs up/down feedback.

    Args:
        github_repo: "owner/repo" string
        item: AnalyzedPaper with .paper.arxiv_id, .paper.title, .relevance.score
        interest_names: Active research interest names for tracking context.
    """
    paper = item.paper  # type: ignore[attr-defined]
    relevance = item.relevance  # type: ignore[attr-defined]

    arxiv_id = paper.arxiv_id
    score = relevance.score
    interest_str = ", ".join(interest_names) if interest_names else ""

    urls: dict[str, str] = {}
    for rating in ("positive", "negative"):
        template = f"feedback_{rating}.yml"
        issue_title = quote(f"[{rating}] {paper.title}")
        paper_encoded = quote(paper.title)
        arxiv_encoded = quote(arxiv_id)
        score_encoded = quote(str(score))
        interest_encoded = quote(interest_str)

        urls[rating] = (
            f"https://github.com/{github_repo}/issues/new"
            f"?template={template}"
            f"&title={issue_title}"
            f"&paper={paper_encoded}"
            f"&arxiv_id={arxiv_encoded}"
            f"&bot_score={score_encoded}"
            f"&interest={interest_encoded}"
        )

    return urls


def build_reference_url(github_repo: str) -> str:
    """Build the GitHub Issue URL for submitting reference papers."""
    return (
        f"https://github.com/{github_repo}/issues/new"
        f"?template=feedback_reference.yml"
    )


def _extract_arxiv_ids(text: str) -> list[str]:
    """Extract unique arxiv IDs from free-form text (URLs or bare IDs)."""
    seen: list[str] = []
    for match in _ARXIV_ID_RE.finditer(text):
        arxiv_id = match.group(1)
        if arxiv_id not in seen:
            seen.append(arxiv_id)
    return seen


def _fetch_reference_metadata(arxiv_ids: list[str]) -> dict[str, tuple[str, str]]:
    """Fetch title + abstract for arxiv IDs. Returns {id: (title, abstract)}."""
    if not arxiv_ids:
        return {}

    meta: dict[str, tuple[str, str]] = {}
    try:
        client = arxiv.Client(delay_seconds=3, num_retries=3)
        search = arxiv.Search(id_list=arxiv_ids)
        for result in client.results(search):
            arxiv_id = result.entry_id.split("/abs/")[-1].split("v")[0]
            meta[arxiv_id] = (
                result.title.replace("\n", " ").strip(),
                result.summary.replace("\n", " ").strip(),
            )
    except Exception:
        logger.exception("Failed to fetch arxiv metadata for reference papers")

    return meta


def _is_reference_issue(issue: dict) -> bool:
    """Return True if an issue is a reference-paper submission.

    The primary signal is the ``reference-paper`` label. As a fallback we detect
    the Issue Form body marker, because GitHub only auto-applies a template label
    if it already exists in the repo — a missing label would otherwise silently
    drop the submission from both the reference loader and the feedback summary.
    """
    labels = [l["name"] for l in issue.get("labels", [])]
    if REFERENCE_LABEL in labels:
        return True
    if "feedback" in labels:
        return False  # a thumbs up/down issue is never a reference submission
    return _REFERENCE_BODY_MARKER in (issue.get("body") or "")


def _fetch_open_issues(repo: str, token: str) -> list[dict]:
    """Fetch all open issues for a repo (paginated)."""
    issues: list[dict] = []
    page = 1
    with httpx.Client(timeout=30) as http:
        while True:
            url = f"{GITHUB_API}/repos/{repo}/issues"
            params = {"state": "open", "per_page": 100, "page": page}
            resp = http.get(url, headers=_github_headers(token), params=params)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            issues.extend(batch)
            page += 1
    return issues


def _references_from_issues(issues: list[dict]) -> list[ReferencePaper]:
    """Build ReferencePaper list from already-fetched reference-paper issues.

    Extracts arxiv IDs from each body and enriches them with title/abstract
    from the arxiv API (capped at MAX_REFERENCE_PAPERS).
    """
    # Map arxiv_id -> note (first occurrence wins).
    id_note: dict[str, str] = {}
    for issue in issues:
        body = issue.get("body", "") or ""
        fields = _parse_issue_body(body)
        links = fields.get("paper_links", body)
        note = fields.get("note", "")
        if note == "_No response_":
            note = ""
        for arxiv_id in _extract_arxiv_ids(links):
            id_note.setdefault(arxiv_id, note)

    if not id_note:
        return []

    # Cap to bound prompt size.
    arxiv_ids = list(id_note)[:MAX_REFERENCE_PAPERS]
    meta = _fetch_reference_metadata(arxiv_ids)

    references: list[ReferencePaper] = []
    for arxiv_id in arxiv_ids:
        title, abstract = meta.get(arxiv_id, ("", ""))
        references.append(
            ReferencePaper(
                arxiv_id=arxiv_id,
                title=title,
                abstract=abstract,
                note=id_note[arxiv_id],
            )
        )
    return references


def load_reference_papers(config: AppConfig, token: str) -> list[ReferencePaper]:
    """Load user-curated reference papers from open GitHub Issues.

    Reads all open reference-paper issues (by label, with a body-marker
    fallback), extracts arxiv IDs from each body, and enriches them with
    title/abstract from the arxiv API.
    """
    repo = config.feedback.github_repo

    # Fetch all open issues (paginated) rather than filtering by label
    # server-side: a missing label would hide submissions, so membership is
    # decided client-side by _is_reference_issue (label or body marker).
    try:
        issues = _fetch_open_issues(repo, token)
    except httpx.HTTPError:
        logger.exception("Failed to fetch reference-paper issues from GitHub")
        return []

    ref_issues = [i for i in issues if _is_reference_issue(i)]
    references = _references_from_issues(ref_issues)
    logger.info("Loaded %d reference papers from GitHub", len(references))
    return references


def format_reference_context(references: list[ReferencePaper]) -> str:
    """Format reference papers into a prompt-injectable context string.

    Returns empty string if there are no reference papers.
    """
    if not references:
        return ""

    parts: list[str] = [
        "## Reference Papers — User-Curated Gold-Standard Examples (HIGHEST PRIORITY)",
        "The user explicitly hand-picked the papers below as the clearest "
        "examples of what they want recommended. This is the SINGLE STRONGEST "
        "relevance signal — it outweighs the keyword/category interests above. "
        "When a candidate shows clear topical or methodological similarity to any "
        "reference paper, treat it as highly relevant: boost its score by roughly "
        "3 points and do not score it below 8. Even partial or emerging "
        "similarity (shared problem, technique, or domain) should pull the score "
        "up by at least 1-2 points. Err on the side of recommending such papers.",
    ]
    for ref in references:
        if ref.title:
            parts.append(f"- \"{ref.title}\" (arXiv:{ref.arxiv_id})")
            if ref.abstract:
                parts.append(f"  {ref.abstract[:400]}")
        else:
            parts.append(f"- arXiv:{ref.arxiv_id}")
        if ref.note:
            parts.append(f"  User note: {ref.note}")

    return "\n".join(parts)


def summarize_and_cleanup(
    config: AppConfig,
    client: genai.Client,
    token: str,
) -> None:
    """Summarize all open feedback + reference papers, save summary, close old issues."""
    repo = config.feedback.github_repo

    # Fetch all open issues once, then partition into thumbs feedback and
    # user-curated reference-paper submissions (both feed the summary).
    all_issues = _fetch_open_issues(repo, token)
    feedback_issues = [
        i for i in all_issues if "feedback" in [l["name"] for l in i.get("labels", [])]
    ]
    reference_issues = [i for i in all_issues if _is_reference_issue(i)]

    if not feedback_issues and not reference_issues:
        logger.info("No open feedback or reference issues to summarize")
        return

    # Parse thumbs up/down entries.
    entries: list[FeedbackEntry] = []
    for issue in feedback_issues:
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
                interest=fields.get("interest", fields.get("matched_interest", "")),
                issue_number=issue.get("number", 0),
                created_at=issue.get("created_at", ""),
            )
        )

    # Enrich reference papers (title/abstract) for the summary prompt.
    references = _references_from_issues(reference_issues)

    # Build summarization prompt
    feedback_text = "\n".join(
        f"- [{e.rating}] \"{e.paper_title}\" (score={e.bot_score}) {e.reason}"
        + (f" [interest: {e.interest}]" if e.interest else "")
        for e in entries
    ) or "(none)"
    reference_text = "\n".join(
        f"- \"{r.title or r.arxiv_id}\" (arXiv:{r.arxiv_id})"
        + (f" — {r.note}" if r.note else "")
        for r in references
    )
    prompt = build_summary_prompt(
        feedback_text, reference_text=reference_text, language=config.language
    )

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
        "total_references": len(references),
        "active_interests": [i.name for i in config.research_interests],
    }
    FEEDBACK_SUMMARY_PATH.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2))
    logger.info(
        "Feedback summary saved (%d feedback entries, %d reference papers)",
        len(entries),
        len(references),
    )

    # Close feedback AND reference issues older than 30 days. Reference issues
    # are folded into the summary above before being closed, so their signal
    # persists via feedback_summary.json even after the direct boost expires.
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    closed = 0
    with httpx.Client(timeout=30) as http:
        for issue in feedback_issues + reference_issues:
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

    logger.info("Closed %d feedback/reference issues older than 30 days", closed)
