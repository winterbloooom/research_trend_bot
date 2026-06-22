"""Propose incremental interests.yaml updates from feedback, as a reviewed PR.

Gathers the same preference signals the scorer uses (learned summary, thumbs
up/down feedback, user-curated reference papers), asks the LLM for a
conservative update to the research-interest list, and — only when something
actually changed — opens a pull request and emails a notification. Never edits
``interests.yaml`` on the main branch directly.
"""

from __future__ import annotations

import base64
import difflib
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from google import genai
from google.genai import types

from research_trend_bot.feedback import (
    GITHUB_API,
    _github_headers,
    load_recent_feedback,
    load_feedback_summary,
    load_reference_papers,
)
from research_trend_bot.models import AppConfig, InterestsProposal, ResearchInterest
from research_trend_bot.prompts.interests_tuning import (
    SYSTEM_PROMPT,
    build_proposal_prompt,
)

logger = logging.getLogger(__name__)

INTERESTS_PATH = Path("interests.yaml")


def _dump_interests(interests: list[ResearchInterest]) -> str:
    """Serialize an interest list to YAML (drops null special_instructions)."""
    data = [i.model_dump(exclude_none=True) for i in interests]
    return yaml.safe_dump(
        {"research_interests": data},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def render_interests_yaml(
    current_path: Path, interests: list[ResearchInterest]
) -> str:
    """Render a full interests.yaml, replacing only the research_interests block.

    Other top-level keys (filtering, language, days_back, ...) are preserved
    from the current file. Inline comments are not preserved — the change is
    reviewed as a PR diff.
    """
    raw = yaml.safe_load(current_path.read_text()) if current_path.exists() else {}
    raw = raw or {}
    raw["research_interests"] = [i.model_dump(exclude_none=True) for i in interests]
    return yaml.safe_dump(
        raw, allow_unicode=True, sort_keys=False, default_flow_style=False
    )


def propose_interests_update(
    config: AppConfig, client: genai.Client, token: str
) -> InterestsProposal | None:
    """Ask the LLM for a conservative interests update. None if no signal/error."""
    summary = load_feedback_summary()
    summary_text = summary.get("summary", "") if summary else ""
    feedback = load_recent_feedback(config, token, days=60)
    references = load_reference_papers(config, token)

    if not feedback and not references and not summary_text:
        logger.info("No feedback/reference signal; skipping interests proposal")
        return None

    feedback_text = "\n".join(
        f"- [{e.rating}] \"{e.paper_title}\" (score={e.bot_score}) {e.reason}"
        + (f" [interest: {e.interest}]" if e.interest else "")
        for e in feedback
    ) or "(none)"
    reference_text = "\n".join(
        f"- \"{r.title or r.arxiv_id}\" (arXiv:{r.arxiv_id})"
        + (f" — {r.note}" if r.note else "")
        for r in references
    )

    prompt = build_proposal_prompt(
        current_interests_yaml=_dump_interests(config.research_interests),
        summary_text=summary_text,
        feedback_text=feedback_text,
        reference_text=reference_text,
        language=config.language,
    )

    try:
        response = client.models.generate_content(
            model=config.llm.analysis_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=InterestsProposal,
                temperature=0.2,
            ),
        )
        proposal: InterestsProposal = response.parsed
    except Exception:
        logger.exception("Failed to generate interests proposal")
        return None

    return proposal


def _normalize(i: ResearchInterest) -> tuple:
    """Order-insensitive key for an interest (keyword/category order ignored)."""
    return (
        i.name,
        sorted(i.keywords),
        sorted(i.arxiv_categories),
        i.special_instructions or "",
    )


def interests_changed(
    current: list[ResearchInterest], proposed: list[ResearchInterest]
) -> bool:
    """True if the proposed interests differ semantically from the current ones.

    Reordering interests or their keywords/categories does not count as a change.
    """
    return sorted(map(_normalize, current)) != sorted(map(_normalize, proposed))


def summarize_changes(
    current: list[ResearchInterest], proposed: list[ResearchInterest]
) -> str:
    """Human-readable semantic diff of two interest lists (added/removed/edited).

    More reviewable than the raw YAML diff, which re-serializes the whole file.
    """
    cur = {i.name: i for i in current}
    new = {i.name: i for i in proposed}
    lines: list[str] = []

    for name in new:
        if name not in cur:
            kws = ", ".join(new[name].keywords)
            lines.append(f"+ NEW interest \"{name}\" (keywords: {kws})")
    for name in cur:
        if name not in new:
            lines.append(f"- REMOVED interest \"{name}\"")
    for name in new:
        if name not in cur:
            continue
        ck, nk = set(cur[name].keywords), set(new[name].keywords)
        cc, nc = set(cur[name].arxiv_categories), set(new[name].arxiv_categories)
        parts: list[str] = []
        if nk - ck:
            parts.append(f"keywords +[{', '.join(sorted(nk - ck))}]")
        if ck - nk:
            parts.append(f"keywords -[{', '.join(sorted(ck - nk))}]")
        if nc - cc:
            parts.append(f"categories +[{', '.join(sorted(nc - cc))}]")
        if cc - nc:
            parts.append(f"categories -[{', '.join(sorted(cc - nc))}]")
        if cur[name].special_instructions != new[name].special_instructions:
            parts.append("special_instructions changed")
        if parts:
            lines.append(f"~ \"{name}\": " + "; ".join(parts))

    return "\n".join(lines) if lines else "(no semantic changes)"


def open_interests_pr(
    repo: str,
    token: str,
    new_content: str,
    rationale: str,
    base: str = "main",
) -> str | None:
    """Open a PR that updates interests.yaml on a new branch. Returns the PR URL.

    Pure GitHub REST API (no git CLI). Requires the token to have
    ``pull-requests: write`` and the repo to allow Actions to create PRs.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"auto/interests-update-{stamp}"
    headers = _github_headers(token)
    path = "interests.yaml"

    try:
        with httpx.Client(timeout=30, headers=headers) as http:
            # Base branch head SHA
            ref = http.get(f"{GITHUB_API}/repos/{repo}/git/ref/heads/{base}")
            ref.raise_for_status()
            base_sha = ref.json()["object"]["sha"]

            # Create the new branch
            http.post(
                f"{GITHUB_API}/repos/{repo}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": base_sha},
            ).raise_for_status()

            # Current file SHA (required to update an existing file)
            cur = http.get(
                f"{GITHUB_API}/repos/{repo}/contents/{path}", params={"ref": base}
            )
            cur.raise_for_status()
            file_sha = cur.json()["sha"]

            # Commit the new content onto the branch
            http.put(
                f"{GITHUB_API}/repos/{repo}/contents/{path}",
                json={
                    "message": "Auto-tune research interests from feedback",
                    "content": base64.b64encode(new_content.encode()).decode(),
                    "sha": file_sha,
                    "branch": branch,
                },
            ).raise_for_status()

            # Open the PR
            pr = http.post(
                f"{GITHUB_API}/repos/{repo}/pulls",
                json={
                    "title": "Auto-tune research interests from feedback",
                    "head": branch,
                    "base": base,
                    "body": (
                        "Automated, conservative update to `interests.yaml` based "
                        "on recent feedback and reference papers.\n\n"
                        "### Rationale\n"
                        f"{rationale}\n\n"
                        "_Review the diff before merging — this PR was generated "
                        "by the weekly interests-tuning job._"
                    ),
                },
            )
            pr.raise_for_status()
            return pr.json()["html_url"]
    except httpx.HTTPError:
        logger.exception("Failed to open interests-update PR")
        return None


def build_proposal_email(
    rationale: str, diff_text: str, pr_url: str | None
) -> tuple[str, str]:
    """Build (html, plain) notification email for a proposed interests update."""
    pr_line = f"PR: {pr_url}" if pr_url else "PR creation failed — see job logs."
    plain = (
        "A research-interest update has been proposed from your feedback.\n\n"
        f"{pr_line}\n\nRationale:\n{rationale}\n\nDiff:\n{diff_text}\n"
    )
    pr_html = (
        f'<p><a href="{pr_url}">Review the pull request</a></p>'
        if pr_url
        else "<p><b>PR creation failed</b> — see job logs.</p>"
    )
    html = (
        "<h2>Proposed research-interest update</h2>"
        "<p>Generated from your recent feedback and reference papers.</p>"
        f"{pr_html}"
        f"<h3>Rationale</h3><pre>{rationale}</pre>"
        f"<h3>Diff</h3><pre>{diff_text}</pre>"
    )
    return html, plain


def unified_diff(old: str, new: str) -> str:
    """Return a unified diff between two interests.yaml texts."""
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="interests.yaml (current)",
            tofile="interests.yaml (proposed)",
        )
    )
