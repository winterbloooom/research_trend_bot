"""Fetch papers from Hugging Face daily_papers API.

HF daily_papers is a curated feed of trending arxiv papers. Most items expose
their arxiv ID as the HF paper slug (e.g. "2501.12345"), which lets us map
them into the same ArxivPaper model used by the arxiv fetcher so downstream
stages (scorer/analyzer/email) don't need to know the source.

Non-arxiv HF papers are skipped — the analyzer needs a PDF URL, and we
currently only resolve PDFs via arxiv.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from research_trend_bot.models import AppConfig, ArxivPaper

logger = logging.getLogger(__name__)

HF_API_URL = "https://huggingface.co/api/daily_papers"

_ARXIV_ID_LIKE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_ARXIV_VERSION_SUFFIX = re.compile(r"v\d+$")
_ARXIV_ABS_URL = re.compile(r"arxiv\.org/abs/([^?#/]+)")


def _strip_version(arxiv_id: str) -> str:
    return _ARXIV_VERSION_SUFFIX.sub("", arxiv_id).strip()


def _extract_arxiv_id(paper_node: dict[str, Any], item: dict[str, Any]) -> str | None:
    """Try to find a canonical arxiv ID for an HF paper item."""
    # 1) HF slug is often the arxiv ID itself
    slug = paper_node.get("id") or item.get("id") or paper_node.get("slug") or item.get("slug")
    if isinstance(slug, str) and _ARXIV_ID_LIKE.match(slug.strip()):
        return _strip_version(slug.strip())

    # 2) Look for arxiv.org/abs/... in any url-like field
    url_candidates = [
        paper_node.get("url"),
        item.get("url"),
        paper_node.get("arxiv_url"),
        item.get("arxiv_url"),
    ]
    for candidate in url_candidates:
        if not isinstance(candidate, str):
            continue
        m = _ARXIV_ABS_URL.search(candidate)
        if m:
            return _strip_version(m.group(1).strip())

    return None


def _normalize_authors(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("full_name") or item.get("author")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
        return names
    return []


def _parse_published(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _item_to_paper(item: dict[str, Any]) -> ArxivPaper | None:
    paper_node = item.get("paper") if isinstance(item.get("paper"), dict) else item

    arxiv_id = _extract_arxiv_id(paper_node, item)
    if not arxiv_id:
        return None

    title = paper_node.get("title") or item.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    abstract = (
        paper_node.get("summary")
        or paper_node.get("abstract")
        or item.get("summary")
        or item.get("abstract")
        or ""
    )
    authors = _normalize_authors(paper_node.get("authors") or item.get("authors"))
    published = _parse_published(
        paper_node.get("publishedAt")
        or paper_node.get("published_at")
        or paper_node.get("submitted")
        or paper_node.get("date")
        or item.get("publishedAt")
        or item.get("published_at")
        or item.get("submitted")
        or item.get("date")
    ) or datetime.now(timezone.utc)

    return ArxivPaper(
        arxiv_id=arxiv_id,
        title=title.replace("\n", " ").strip(),
        authors=authors,
        abstract=str(abstract).replace("\n", " ").strip(),
        categories=[],  # HF doesn't expose arxiv categories
        published=published,
        updated=published,
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        abs_url=f"https://arxiv.org/abs/{arxiv_id}",
        source="huggingface",
    )


def fetch_hf_papers(config: AppConfig, days_back: int) -> list[ArxivPaper]:
    """Fetch HF daily_papers within the given days_back window.

    Returns an empty list (not an exception) on any HF-side failure so the
    pipeline can continue with arxiv-only results.
    """
    if not config.huggingface.enabled:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    limit = config.huggingface.limit
    max_pages = config.huggingface.max_pages

    papers: list[ArxivPaper] = []
    seen_ids: set[str] = set()
    skipped_non_arxiv = 0

    try:
        with httpx.Client(timeout=30, follow_redirects=True) as http:
            for page in range(max_pages):
                if len(papers) >= limit:
                    break

                url = HF_API_URL if page == 0 else f"{HF_API_URL}?p={page}"
                try:
                    resp = http.get(url)
                    resp.raise_for_status()
                    payload = resp.json()
                except (httpx.HTTPError, ValueError) as e:
                    logger.warning("HF fetch failed on page %d: %s", page, e)
                    break

                if isinstance(payload, list):
                    items = payload
                elif isinstance(payload, dict):
                    items = payload.get("data") or payload.get("papers") or payload.get("items") or []
                else:
                    items = []

                if not items:
                    break

                older_than_cutoff_on_page = 0
                for item in items:
                    if not isinstance(item, dict):
                        continue

                    paper = _item_to_paper(item)
                    if paper is None:
                        skipped_non_arxiv += 1
                        continue

                    if paper.published < cutoff:
                        older_than_cutoff_on_page += 1
                        continue

                    if paper.arxiv_id in seen_ids:
                        continue
                    seen_ids.add(paper.arxiv_id)
                    papers.append(paper)

                    if len(papers) >= limit:
                        break

                # If an entire page is older than the cutoff, stop paginating.
                if older_than_cutoff_on_page == len(items):
                    break
    except Exception:
        logger.exception("HF fetch unexpectedly failed; continuing without HF papers")
        return []

    logger.info(
        "HF papers fetched: %d (skipped non-arxiv: %d, days_back=%d)",
        len(papers),
        skipped_non_arxiv,
        days_back,
    )
    return papers
