"""Stage 1 prompt: batch abstract relevance scoring."""

SYSTEM_PROMPT = """\
You are a research paper relevance scorer. Given the user's research interests \
and a batch of paper abstracts, score each paper's relevance from 1 to 10.

Scoring guidelines:
- 9-10: Directly addresses the user's core research topic
- 7-8: Highly relevant, closely related methods or applications
- 5-6: Moderately relevant, shares some overlap
- 3-4: Tangentially related
- 1-2: Not relevant"""

_LANGUAGE_INSTRUCTIONS = {
    "ko": "Write the reasoning in Korean. Keep technical terms (e.g., Transformer, diffusion, NeRF) in English as-is.",
    "en": "Write the reasoning in English.",
}


def build_scoring_prompt(
    interests_description: str,
    papers: list[dict],
    language: str = "ko",
) -> str:
    """Build the user message for batch abstract scoring.

    Args:
        interests_description: Formatted string describing research interests.
        papers: List of dicts with 'arxiv_id', 'title', 'abstract'.
        language: Output language code ('ko' or 'en').
    """
    papers_text = ""
    for i, p in enumerate(papers, 1):
        papers_text += (
            f"\n--- Paper {i} ---\n"
            f"ID: {p['arxiv_id']}\n"
            f"Title: {p['title']}\n"
            f"Abstract: {p['abstract']}\n"
        )

    lang_instruction = _LANGUAGE_INSTRUCTIONS.get(language, _LANGUAGE_INSTRUCTIONS["en"])

    return f"""\
## My Research Interests
{interests_description}

## Papers to Score
{papers_text}

## Instructions
{lang_instruction}

For each paper, provide:
- "arxiv_id": the paper ID (must match exactly)
- "score": integer 1-10
- "reasoning": one sentence explaining the score
- "matched_keywords": list of matched keywords from my interests (empty list if none)"""
