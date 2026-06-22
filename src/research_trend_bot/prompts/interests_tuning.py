"""Prompt for proposing incremental updates to interests.yaml."""

SYSTEM_PROMPT = """\
You are a research-interest curator. Given the user's CURRENT tracked research \
interests and recent signals about their true preferences (thumbs up/down \
feedback, a learned preference summary, and hand-picked reference papers), \
propose a CONSERVATIVE, incremental update to the interest list.

Rules:
- Make only well-justified changes. If the signals don't clearly warrant a \
change, set changed=false and return the interests EXACTLY as given.
- Prefer small edits: add or remove a few keywords, or refine a \
special_instructions string.
- You MAY add a brand-new interest or remove an existing one, but ONLY with \
strong, repeated evidence across multiple signals. Never drop an interest on a \
single weak signal.
- Reference papers are the STRONGEST signal — make sure the interests and \
keywords cover the topics those papers exemplify.
- Negative feedback that recurs is evidence to narrow keywords or add a \
special_instruction to exclude that sub-topic; do not over-react to one item.
- Keep arxiv_categories valid arXiv codes (e.g. cs.AI, cs.LG, cs.CV, cs.CL, \
cs.GR, cs.HC, cs.MA, cs.SE) and only change them when clearly justified.
- Output the COMPLETE interest list, not a diff."""

_LANGUAGE_INSTRUCTIONS = {
    "ko": "Write the rationale in Korean. Keep technical terms in English as-is.",
    "en": "Write the rationale in English.",
}


def build_proposal_prompt(
    current_interests_yaml: str,
    summary_text: str,
    feedback_text: str,
    reference_text: str,
    language: str = "ko",
) -> str:
    """Build the prompt for proposing an interests.yaml update.

    Args:
        current_interests_yaml: The current research_interests block as YAML.
        summary_text: Learned preference summary (may be empty).
        feedback_text: Formatted thumbs up/down entries (may be "(none)").
        reference_text: Formatted reference papers (may be empty).
        language: Output language for the rationale ('ko' or 'en').
    """
    lang_instruction = _LANGUAGE_INSTRUCTIONS.get(language, _LANGUAGE_INSTRUCTIONS["en"])

    return f"""\
## Current Research Interests (YAML)
{current_interests_yaml}

## Learned Preference Summary
{summary_text or "(none yet)"}

## Thumbs Up/Down Feedback
{feedback_text}

## User-Curated Reference Papers (strongest signal)
{reference_text or "(none)"}

## Instructions
{lang_instruction}

Decide whether the current interests should change given the signals above.
- If yes, return the full updated interest list and set changed=true.
- If no change is clearly warranted, return the interests unchanged and set
  changed=false.
Keep the rationale to 2-4 short bullet points describing each concrete change
(or stating that no change is warranted)."""
