"""Stage 2 prompt: full paper analysis via PDF."""

SYSTEM_PROMPT = """\
You are a senior research scientist who reads academic papers and produces \
structured, insightful analyses. You write clearly and concisely, focusing on \
what matters most to practitioners and researchers."""

_LANGUAGE_INSTRUCTIONS = {
    "ko": (
        "Write all analysis in Korean. "
        "Keep technical terms (e.g., Transformer, diffusion, NeRF, attention, latent space) in English as-is."
    ),
    "en": "Write all analysis in English.",
}


def build_analysis_prompt(interests_description: str, language: str = "ko") -> str:
    """Build the user message for full PDF analysis.

    The PDF itself is attached as a separate content block.

    Args:
        interests_description: Formatted string describing research interests.
        language: Output language code ('ko' or 'en').
    """
    lang_instruction = _LANGUAGE_INSTRUCTIONS.get(language, _LANGUAGE_INSTRUCTIONS["en"])

    return f"""\
## My Research Interests
{interests_description}

## Instructions
Read the attached paper thoroughly and produce a structured analysis.

{lang_instruction}

Use bullet points (each line starting with "- ") for the following fields: \
problem_and_motivation, core_idea, method, experiments_and_results, limitations, personal_relevance. \
Each bullet should be 1-2 sentences. Do NOT write paragraphs for these fields.

For each field:
- "arxiv_id": the paper's arxiv ID (from the PDF header)
- "title": exact paper title
- "authors": list of author names
- "affiliations": list of unique institutional affiliations (e.g., ["Google DeepMind", "MIT"])
- "keywords": 3-7 key technical terms or concepts
- "task": what task or problem does this paper address? (1 sentence)
- "problem_and_motivation": what problem exists and why does it matter? (2-3 bullets)
- "core_idea": what is the key insight or contribution? (2-3 bullets)
- "method": how does the approach work? describe the architecture or algorithm (3-5 bullets)
- "experiments_and_results": what experiments were run and what were the key results? (3-5 bullets)
- "limitations": what are the limitations or open questions? (2-3 bullets)
- "personal_relevance": why does this paper matter for my research interests listed above? be specific about connections (2-3 bullets)"""
