"""Stage 2: Full paper analysis via PDF using Gemini."""

from __future__ import annotations

import io
import logging
import time

import httpx
from google import genai
from google.genai import types
from pypdf import PdfReader
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from research_trend_bot.models import (
    AnalyzedPaper,
    AppConfig,
    PaperAnalysis,
    ScoredPaper,
)
from research_trend_bot.prompts.analysis import SYSTEM_PROMPT, build_analysis_prompt

logger = logging.getLogger(__name__)

MAX_PDF_PAGES = 30
MAX_PDF_SIZE_MB = 20  # Gemini inline limit ~20MB


def _format_interests(config: AppConfig) -> str:
    lines: list[str] = []
    for interest in config.research_interests:
        kw = ", ".join(interest.keywords)
        lines.append(f"- **{interest.name}**: [{kw}]")
        if interest.special_instructions:
            lines.append(f"  > Special instructions: {interest.special_instructions}")

    if config.special_instructions:
        lines.append("")
        lines.append("## Additional Instructions")
        lines.append(config.special_instructions)

    return "\n".join(lines)


def _download_pdf(url: str) -> bytes | None:
    """Download a PDF, returning bytes or None on failure."""
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as http:
            resp = http.get(url)
            resp.raise_for_status()
            data = resp.content
    except httpx.HTTPError:
        logger.exception("Failed to download PDF: %s", url)
        return None

    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_PDF_SIZE_MB:
        logger.warning("PDF too large (%.1f MB > %d MB): %s", size_mb, MAX_PDF_SIZE_MB, url)
        return None

    return data


def _check_page_count(pdf_data: bytes) -> int | None:
    """Return page count, or None if the PDF can't be read."""
    try:
        reader = PdfReader(io.BytesIO(pdf_data))
        return len(reader.pages)
    except Exception:
        logger.exception("Failed to read PDF")
        return None


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=2, min=5, max=60),
    stop=stop_after_attempt(3),
    before_sleep=lambda retry_state: logger.info(
        "Retrying API call (attempt %d)...", retry_state.attempt_number
    ),
)
def _analyze_single(
    client: genai.Client,
    config: AppConfig,
    scored_paper: ScoredPaper,
    interests_desc: str,
) -> PaperAnalysis | None:
    """Analyze a single paper via its PDF."""
    paper = scored_paper.paper

    # Download PDF
    pdf_data = _download_pdf(paper.pdf_url)
    if pdf_data is None:
        return None

    # Check page count
    page_count = _check_page_count(pdf_data)
    if page_count is None:
        return None
    if page_count > MAX_PDF_PAGES:
        logger.warning(
            "Skipping %s: too many pages (%d > %d)", paper.arxiv_id, page_count, MAX_PDF_PAGES
        )
        return None

    logger.info("Analyzing %s (%d pages): %s", paper.arxiv_id, page_count, paper.title)

    user_prompt = build_analysis_prompt(interests_desc, language=config.language)

    response = client.models.generate_content(
        model=config.llm.analysis_model,
        contents=[
            types.Part.from_bytes(data=pdf_data, mime_type="application/pdf"),
            user_prompt,
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=PaperAnalysis,
            temperature=0.3,
        ),
    )

    try:
        parsed: PaperAnalysis = response.parsed
        # Ensure arxiv_id is correct
        parsed.arxiv_id = paper.arxiv_id
        return parsed
    except Exception:
        logger.error("Failed to parse analysis for %s: %s", paper.arxiv_id, response.text[:500])
        return None


def analyze_papers(
    client: genai.Client,
    config: AppConfig,
    scored_papers: list[ScoredPaper],
) -> list[AnalyzedPaper]:
    """Analyze all scored papers, skipping those that fail."""
    if not scored_papers:
        return []

    interests_desc = _format_interests(config)
    results: list[AnalyzedPaper] = []

    for idx, sp in enumerate(scored_papers):
        if idx > 0:
            time.sleep(7)  # Rate limit: ~10 RPM for Flash

        try:
            analysis = _analyze_single(client, config, sp, interests_desc)
            if analysis:
                results.append(
                    AnalyzedPaper(
                        paper=sp.paper,
                        relevance=sp.relevance,
                        analysis=analysis,
                    )
                )
        except Exception:
            logger.exception("Failed to analyze paper %s", sp.paper.arxiv_id)

    logger.info("Analysis complete: %d/%d papers analyzed", len(results), len(scored_papers))
    return results
