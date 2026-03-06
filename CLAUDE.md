# CLAUDE.md

Automated research paper digest bot — fetches arxiv papers, scores relevance with Gemini, analyzes top papers via PDF, and emails a structured digest.

## Quick commands

```bash
# Run the full pipeline
python -m research_trend_bot.main              # uses config.yaml
python -m research_trend_bot.main config.yaml  # explicit path

# Run biweekly feedback summarization
python -m research_trend_bot.feedback_cli config.yaml

# Install in dev mode
pip install -e .
```

## Project structure

```
src/research_trend_bot/
  main.py          # Pipeline orchestrator (fetch → score → analyze → email)
  config.py        # YAML config loader + env secret helpers
  models.py        # Pydantic models (config + pipeline data)
  fetcher.py       # arXiv API paper fetching with adaptive days_back
  scorer.py        # Stage 1: batch abstract scoring via Gemini (with keyword pre-filter)
  analyzer.py      # Stage 2: full PDF analysis via Gemini
  email_builder.py # Jinja2 HTML + plain-text email builder
  sender.py        # SMTP email sender
  feedback.py      # GitHub Issue-based feedback: load, format, summarize, URL builder
  feedback_cli.py  # CLI entry point for biweekly feedback summarization
  templates/
    newsletter.html # Jinja2 email template (packaged with the module)
  prompts/
    scoring.py     # Scoring system/user prompts
    analysis.py    # Analysis system/user prompts
    feedback_summary.py  # Feedback summarization prompt
config.example.yaml
feedback_summary.json                        # LLM-generated feedback summary (auto-updated)
.github/workflows/daily_digest.yml           # GitHub Actions cron (weekdays KST 11:00 / UTC 02:00)
.github/workflows/feedback_summary.yml       # Biweekly feedback summary (1st & 15th, UTC 03:00)
```

## Key dependencies

- `google-genai` — Gemini API client
- `arxiv` — arXiv search API
- `pydantic` — data models and validation
- `jinja2` / `markupsafe` — email template rendering
- `httpx` — PDF download + GitHub API calls (feedback)
- `pypdf` — PDF page count validation
- `tenacity` — retry logic for analysis API calls
- `python-dotenv` — .env secret loading

## Configuration

- **Config**: `config.yaml` (gitignored) — copy from `config.example.yaml`
- **Secrets**: `.env` file with `GEMINI_API_KEY`, `SMTP_PASSWORD`, and optionally `GITHUB_TOKEN`
- Two levels of `special_instructions`: per-interest and global
- `language`: `"ko"` (Korean, default) or `"en"`
- `days_back` auto-expands up to 7 if no papers found (e.g., weekends)
- **Feedback** (opt-in): `feedback.enabled: true` + `feedback.github_repo: "owner/repo"` — collects thumbs up/down via GitHub Issues; disabled by default with zero impact on existing behavior

## Testing rules

- **Minimize API usage**: This project uses a free-tier Gemini API key with strict rate limits (20 RPM). When running tests, keep API calls to the bare minimum — just enough to verify things work. Use `top_k: 1` or similar to reduce analysis calls.
- **Always use a virtual environment**: Never install dependencies directly into the system Python. Use `venv` or equivalent before installing anything.

## Important notes

- Scorer uses keyword pre-filter before LLM calls to save API quota
- Scorer batch size is 25 (not 10) — optimized for free-tier rate limits
- Scorer has built-in 429 retry that respects Gemini's `retryDelay`
- Analyzer passes raw PDF bytes to Gemini via `Part.from_bytes()` — PDFs >20 MB or >30 pages are skipped
- `PdfReader` requires `io.BytesIO()` wrapper around raw bytes
- `config.yaml` is gitignored; `config.example.yaml` is committed
- The `email_builder.py` `bulletize` Jinja2 filter converts `"- "` prefixed lines to `<ul><li>` HTML
- Feedback system is fully opt-in (`feedback.enabled: false` by default) — when disabled, `feedback_context=""` is passed through scorer/analyzer with no prompt changes and no email buttons rendered
- Feedback Issue body uses `**Key**: value` format parsed by `_parse_issue_body()` regex
- `feedback_summary.json` is committed to repo and auto-updated by the biweekly workflow
