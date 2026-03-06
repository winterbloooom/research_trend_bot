"""Stage 1: Score paper abstracts for relevance using Gemini."""

from __future__ import annotations

import logging
import re
import time

from google import genai
from google.genai import types
from google.genai.errors import ClientError
from pydantic import BaseModel

from research_trend_bot.models import (
    AppConfig,
    ArxivPaper,
    RelevanceScore,
    ScoredPaper,
)
from research_trend_bot.prompts.scoring import SYSTEM_PROMPT, build_scoring_prompt

logger = logging.getLogger(__name__)

BATCH_SIZE = 25


class ScoringResponse(BaseModel):
    """Wrapper model for batch scoring response."""

    scores: list[RelevanceScore]


def _format_interests(config: AppConfig) -> str:
    """Format research interests for the prompt."""
    lines: list[str] = []
    for interest in config.research_interests:
        kw = ", ".join(interest.keywords)
        cats = ", ".join(interest.arxiv_categories)
        lines.append(f"- **{interest.name}**: keywords=[{kw}], categories=[{cats}]")
        if interest.special_instructions:
            lines.append(f"  > Special instructions: {interest.special_instructions}")

    if config.special_instructions:
        lines.append("")
        lines.append("## Additional Instructions")
        lines.append(config.special_instructions)

    return "\n".join(lines)


def _score_batch(
    client: genai.Client,
    config: AppConfig,
    papers: list[ArxivPaper],
    interests_desc: str,
) -> list[RelevanceScore]:
    """Score a single batch of papers."""
    papers_data = [
        {"arxiv_id": p.arxiv_id, "title": p.title, "abstract": p.abstract}
        for p in papers
    ]

    prompt = build_scoring_prompt(interests_desc, papers_data, language=config.language)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=config.llm.scoring_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=ScoringResponse,
                    temperature=0.2,
                ),
            )
            break  # success
        except ClientError as e:
            if e.code == 429 and attempt < max_retries - 1:
                # Extract retryDelay from error message
                match = re.search(r"retryDelay.*?(\d+)", str(e))
                wait = int(match.group(1)) + 5 if match else 30
                logger.warning("Rate limited, waiting %ds before retry...", wait)
                time.sleep(wait)
            else:
                logger.exception("Gemini API call failed for scoring batch")
                return []
        except Exception:
            logger.exception("Gemini API call failed for scoring batch")
            return []

    try:
        parsed: ScoringResponse = response.parsed
        return parsed.scores
    except Exception:
        logger.error("Failed to parse scoring response: %s", response.text[:500])
        return []


def _keyword_prefilter(
    config: AppConfig, papers: list[ArxivPaper]
) -> list[ArxivPaper]:
    """Pre-filter papers that contain at least one keyword in title or abstract.

    This avoids wasting LLM calls on clearly irrelevant papers.
    """
    all_keywords: list[str] = []
    for interest in config.research_interests:
        all_keywords.extend(kw.lower() for kw in interest.keywords)

    filtered: list[ArxivPaper] = []
    for paper in papers:
        text = f"{paper.title} {paper.abstract}".lower()
        if any(kw in text for kw in all_keywords):
            filtered.append(paper)

    dropped = len(papers) - len(filtered)
    if dropped:
        logger.info(
            "Keyword pre-filter: %d/%d papers passed (%d dropped)",
            len(filtered),
            len(papers),
            dropped,
        )

    return filtered


def score_papers(
    client: genai.Client,
    config: AppConfig,
    papers: list[ArxivPaper],
    feedback_context: str = "",
) -> list[ScoredPaper]:
    """Score all papers in batches and return those above threshold, sorted by score."""
    if not papers:
        return []

    papers = _keyword_prefilter(config, papers)
    if not papers:
        return []

    interests_desc = _format_interests(config)
    if feedback_context:
        interests_desc += "\n\n" + feedback_context
    all_scores: dict[str, RelevanceScore] = {}

    for i in range(0, len(papers), BATCH_SIZE):
        batch = papers[i : i + BATCH_SIZE]
        logger.info("Scoring batch %d-%d of %d papers", i + 1, i + len(batch), len(papers))

        if i > 0:
            time.sleep(5)  # Rate limit: ~15 RPM for Flash-Lite

        scores = _score_batch(client, config, batch, interests_desc)
        for s in scores:
            all_scores[s.arxiv_id] = s

    # Match scores to papers and filter
    paper_map = {p.arxiv_id: p for p in papers}
    scored: list[ScoredPaper] = []
    for arxiv_id, score in all_scores.items():
        if arxiv_id in paper_map and score.score >= config.filtering.score_threshold:
            scored.append(ScoredPaper(paper=paper_map[arxiv_id], relevance=score))

    # Sort by score descending, take top_k
    scored.sort(key=lambda sp: sp.relevance.score, reverse=True)
    top = scored[: config.filtering.top_k]

    logger.info(
        "Scoring complete: %d/%d papers above threshold (%d), top %d selected",
        len(scored),
        len(papers),
        config.filtering.score_threshold,
        len(top),
    )

    return top
