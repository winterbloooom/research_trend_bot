"""Prompt for biweekly feedback summarization."""

SYSTEM_PROMPT = """\
You are a research preference analyst. Given user feedback on recommended \
research papers, summarize the patterns in their preferences to help improve \
future recommendations."""

_LANGUAGE_INSTRUCTIONS = {
    "ko": (
        "Write the summary in Korean. "
        "Keep technical terms (e.g., Transformer, diffusion, NeRF) in English as-is."
    ),
    "en": "Write the summary in English.",
}


def build_summary_prompt(
    feedback_text: str, reference_text: str = "", language: str = "ko"
) -> str:
    """Build prompt for summarizing feedback patterns.

    Args:
        feedback_text: Formatted thumbs up/down feedback entries.
        reference_text: Formatted user-curated reference papers (optional).
        language: Output language code ('ko' or 'en').
    """
    lang_instruction = _LANGUAGE_INSTRUCTIONS.get(language, _LANGUAGE_INSTRUCTIONS["en"])

    reference_section = ""
    if reference_text:
        reference_section = f"""

## User-Curated Reference Papers (Gold-Standard Examples)
The user explicitly hand-picked these papers as exactly the kind of work they \
want recommended. Treat them as the STRONGEST positive signal — weight them \
more heavily than the thumbs up/down feedback when inferring preferences.
{reference_text}"""

    return f"""\
## User Feedback on Recommended Papers
{feedback_text}{reference_section}

## Instructions
{lang_instruction}

Analyze the feedback patterns and produce a concise summary (3-5 bullet points, each starting with "- "). Focus on:
- The topics/methods exemplified by the user-curated reference papers (the strongest positive signal)
- What topics/methods the user consistently finds relevant or irrelevant
- Any gap between bot scores and user preferences
- Actionable insights for improving future scoring

Keep each bullet to 1-2 sentences. Be specific about paper topics, not generic."""
