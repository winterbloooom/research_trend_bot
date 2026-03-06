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


def build_summary_prompt(feedback_text: str, language: str = "ko") -> str:
    """Build prompt for summarizing feedback patterns.

    Args:
        feedback_text: Formatted feedback entries.
        language: Output language code ('ko' or 'en').
    """
    lang_instruction = _LANGUAGE_INSTRUCTIONS.get(language, _LANGUAGE_INSTRUCTIONS["en"])

    return f"""\
## User Feedback on Recommended Papers
{feedback_text}

## Instructions
{lang_instruction}

Analyze the feedback patterns and produce a concise summary (3-5 bullet points, each starting with "- "). Focus on:
- What topics/methods the user consistently finds relevant or irrelevant
- Any gap between bot scores and user preferences
- Actionable insights for improving future scoring

Keep each bullet to 1-2 sentences. Be specific about paper topics, not generic."""
