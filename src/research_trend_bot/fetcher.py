"""Fetch papers from arxiv + Hugging Face and merge into a single deduped list."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import arxiv

from research_trend_bot.hf_fetcher import fetch_hf_papers
from research_trend_bot.models import AppConfig, ArxivPaper

logger = logging.getLogger(__name__)


def _build_query(categories: list[str], start_date: datetime, end_date: datetime) -> str:
    """Build an arxiv API query string for categories and date range."""
    cat_query = " OR ".join(f"cat:{cat}" for cat in categories)
    date_from = start_date.strftime("%Y%m%d%H%M%S")
    date_to = end_date.strftime("%Y%m%d%H%M%S")
    return f"({cat_query}) AND submittedDate:[{date_from} TO {date_to}]"


def _result_to_paper(result: arxiv.Result) -> ArxivPaper:
    """Convert an arxiv.Result to our ArxivPaper model."""
    return ArxivPaper(
        arxiv_id=result.entry_id.split("/abs/")[-1],
        title=result.title.replace("\n", " ").strip(),
        authors=[a.name for a in result.authors],
        abstract=result.summary.replace("\n", " ").strip(),
        categories=result.categories,
        published=result.published,
        updated=result.updated,
        pdf_url=result.pdf_url,
        abs_url=result.entry_id,
    )


MAX_DAYS_BACK = 7


def _fetch_arxiv_with_days_back(config: AppConfig, days_back: int) -> list[ArxivPaper]:
    """Fetch arxiv papers for all research interests with a specific days_back window."""
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days_back)
    end_date = now

    client = arxiv.Client(page_size=100, delay_seconds=3, num_retries=3)
    seen_ids: set[str] = set()
    papers: list[ArxivPaper] = []

    for interest in config.research_interests:
        query = _build_query(interest.arxiv_categories, start_date, end_date)
        logger.info(
            "Fetching papers for '%s': %s (max %d)",
            interest.name,
            query,
            config.filtering.max_papers_per_interest,
        )

        search = arxiv.Search(
            query=query,
            max_results=config.filtering.max_papers_per_interest,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )

        count = 0
        try:
            for result in client.results(search):
                paper = _result_to_paper(result)
                if paper.arxiv_id not in seen_ids:
                    seen_ids.add(paper.arxiv_id)
                    papers.append(paper)
                    count += 1
        except Exception:
            logger.exception("Error fetching papers for '%s'", interest.name)

        logger.info("Fetched %d new papers for '%s'", count, interest.name)

    logger.info("Total unique arxiv papers fetched: %d (days_back=%d)", len(papers), days_back)
    return papers


def _merge_and_dedupe(
    arxiv_papers: list[ArxivPaper], hf_papers: list[ArxivPaper]
) -> list[ArxivPaper]:
    """Merge arxiv + HF papers, deduplicating by arxiv_id.

    When a paper appears in both sources, the arxiv entry wins (it carries
    richer metadata — categories, published/updated timestamps from the arxiv
    API) but its ``source`` is upgraded to "both" to signal the HF overlap.
    HF-only papers (arxiv IDs not returned by the category queries) are
    appended as-is with ``source="huggingface"``.
    """
    by_id: dict[str, ArxivPaper] = {}

    for paper in arxiv_papers:
        by_id[paper.arxiv_id] = paper

    overlap = 0
    hf_only = 0
    for paper in hf_papers:
        existing = by_id.get(paper.arxiv_id)
        if existing is None:
            by_id[paper.arxiv_id] = paper
            hf_only += 1
        else:
            if existing.source != "both":
                by_id[paper.arxiv_id] = existing.model_copy(update={"source": "both"})
            overlap += 1

    logger.info(
        "Merged papers: %d total (arxiv=%d, hf_only=%d, overlap=%d)",
        len(by_id),
        len(arxiv_papers),
        hf_only,
        overlap,
    )
    return list(by_id.values())


def fetch_papers(config: AppConfig) -> list[ArxivPaper]:
    """Fetch papers from all configured sources, expanding the date window up
    to MAX_DAYS_BACK if nothing is found.

    Merges arxiv (category-based) and Hugging Face daily_papers (curated)
    results, deduplicating by arxiv_id.
    """
    days_back = config.days_back

    while days_back <= MAX_DAYS_BACK:
        arxiv_papers = _fetch_arxiv_with_days_back(config, days_back)
        hf_papers = fetch_hf_papers(config, days_back)
        merged = _merge_and_dedupe(arxiv_papers, hf_papers)
        if merged:
            return merged

        next_days = min(days_back + 2, MAX_DAYS_BACK + 1)
        if next_days > MAX_DAYS_BACK:
            break
        logger.info(
            "No papers found with days_back=%d, expanding to %d", days_back, next_days
        )
        days_back = next_days

    return []
