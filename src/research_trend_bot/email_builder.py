"""Build HTML email content from digest report using Jinja2."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

from research_trend_bot.models import DigestReport

logger = logging.getLogger(__name__)

# Template directory: <package>/templates/
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _bulletize(text: str) -> Markup:
    """Convert bullet-point text to an HTML <ul> list.

    Falls back to <br>-separated paragraph if no bullets detected.
    Handles cases where the LLM concatenates bullets without newlines
    (e.g., "...sentence.- next bullet").
    """
    # Normalize: insert newline before "- " when glued after punctuation
    text = re.sub(r"(?<=[.!?。])\s*- ", "\n- ", text)
    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
    bullets = [line for line in lines if line.startswith("- ")]

    if bullets and len(bullets) >= len(lines) * 0.5:
        items = []
        for line in lines:
            content = line[2:] if line.startswith("- ") else line
            items.append(
                f'<li style="margin-bottom:3px;">{escape(content)}</li>'
            )
        return Markup(
            '<ul style="margin:4px 0 0;padding-left:18px;font-size:13px;'
            f'color:#333;line-height:1.6;">{"".join(items)}</ul>'
        )

    escaped_lines = [str(escape(line)) for line in lines]
    return Markup(
        '<p style="margin:4px 0 0;font-size:13px;color:#333;line-height:1.5;">'
        f'{"<br>".join(escaped_lines)}</p>'
    )


def _build_plain_text(report: DigestReport) -> str:
    """Build a plain-text fallback of the digest."""
    lines = [
        f"Research Digest - {report.generated_at.strftime('%Y-%m-%d')}",
        f"{len(report.papers)} paper(s) from {report.total_fetched} scanned",
        "=" * 60,
        "",
    ]

    for item in report.papers:
        a = item.analysis
        lines.extend([
            f"[{item.relevance.score}/10] {a.title}",
            f"Authors: {', '.join(a.authors[:3])}{'...' if len(a.authors) > 3 else ''}",
            f"Affiliations: {', '.join(a.affiliations)}" if a.affiliations else "",
            f"Keywords: {', '.join(a.keywords)}",
            "",
            f"Task: {a.task}",
            "",
            f"Problem & Motivation:",
            a.problem_and_motivation,
            "",
            f"Core Idea:",
            a.core_idea,
            "",
            f"Method:",
            a.method,
            "",
            f"Experiments & Results:",
            a.experiments_and_results,
            "",
            f"Limitations:",
            a.limitations,
            "",
            f"Why it matters:",
            a.personal_relevance,
            "",
            f"PDF: {item.paper.pdf_url}",
            f"arXiv: {item.paper.abs_url}",
            "-" * 60,
            "",
        ])

    return "\n".join(lines)


def build_email(report: DigestReport) -> tuple[str, str]:
    """Build HTML and plain-text email bodies.

    Returns:
        (html_body, plain_text_body)
    """
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=True,
    )
    env.filters["bulletize"] = _bulletize

    template = env.get_template("newsletter.html")

    html = template.render(
        generated_at=report.generated_at,
        research_interests=report.research_interests,
        total_fetched=report.total_fetched,
        total_scored=report.total_scored,
        papers=report.papers,
    )

    plain = _build_plain_text(report)

    logger.info("Email built: %d chars HTML, %d chars plain text", len(html), len(plain))
    return html, plain
