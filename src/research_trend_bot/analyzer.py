"""Stage 2: Full paper analysis via PDF using Gemini."""

from __future__ import annotations

import io
import logging
import time

import httpx
from google import genai
from google.genai import errors, types
from pypdf import PdfReader
from tenacity import (
    retry,
    retry_if_exception,
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

MAX_PDF_PAGES = 50
MAX_PDF_SIZE_MB = 20  # Gemini inline limit ~20MB

# Overload / rate-limit statuses worth retrying (and falling back on). Other
# API errors (e.g. 400 bad request) fail fast since retrying won't help.
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


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


def _is_transient(exc: BaseException) -> bool:
    """True for errors that may succeed on retry (overload, rate limit, network)."""
    if isinstance(exc, errors.APIError):
        return exc.code in TRANSIENT_STATUS_CODES
    return isinstance(exc, httpx.TransportError)


@retry(
    retry=retry_if_exception(_is_transient),
    wait=wait_exponential(multiplier=10, min=10, max=40),
    stop=stop_after_attempt(3),
    reraise=True,
    before_sleep=lambda retry_state: logger.info(
        "Transient API error (%s), retrying (attempt %d)...",
        retry_state.outcome.exception(),
        retry_state.attempt_number,
    ),
)
def _generate_analysis(
    client: genai.Client,
    model: str,
    pdf_data: bytes,
    user_prompt: str,
) -> types.GenerateContentResponse:
    """Call Gemini on a PDF, retrying only on transient errors."""
    return client.models.generate_content(
        model=model,
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


def _analyze_single(
    client: genai.Client,
    config: AppConfig,
    scored_paper: ScoredPaper,
    interests_desc: str,
    models: list[str],
) -> PaperAnalysis | None:
    """Analyze a single paper via its PDF.

    ``models`` is tried in order: when a model still fails with transient
    errors after its retries, it is dropped from ``models`` (in place) so later
    papers go straight to the next model instead of re-waiting on an overloaded
    one. Non-transient errors propagate to the caller.
    """
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

    user_prompt = build_analysis_prompt(interests_desc, language=config.language)

    while models:
        model = models[0]
        logger.info(
            "Analyzing %s (%d pages) with %s: %s",
            paper.arxiv_id, page_count, model, paper.title,
        )
        try:
            response = _generate_analysis(client, model, pdf_data, user_prompt)
            break
        except Exception as exc:
            if not _is_transient(exc):
                raise
            models.pop(0)
            if models:
                logger.warning(
                    "%s unavailable (%s); falling back to %s for remaining papers",
                    model, exc, models[0],
                )
            else:
                logger.error("All analysis models unavailable (%s)", exc)
                return None

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
    feedback_context: str = "",
) -> list[AnalyzedPaper]:
    """Analyze all scored papers, skipping those that fail."""
    if not scored_papers:
        return []

    interests_desc = _format_interests(config)
    if feedback_context:
        interests_desc += "\n\n" + feedback_context
    results: list[AnalyzedPaper] = []
    models = [config.llm.analysis_model]
    fallback = config.llm.analysis_fallback_model
    if fallback and fallback != config.llm.analysis_model:
        models.append(fallback)

    for idx, sp in enumerate(scored_papers):
        if not models:
            logger.warning("Skipping %s: no analysis model available", sp.paper.arxiv_id)
            continue
        if idx > 0:
            time.sleep(7)  # Rate limit: ~10 RPM for Flash

        try:
            analysis = _analyze_single(client, config, sp, interests_desc, models)
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
