# CLAUDE.md

Automated research paper digest bot — fetches papers from arxiv + Hugging Face daily_papers, scores relevance with Gemini, analyzes top papers via PDF, and emails a structured digest.

## Quick commands

```bash
# Run the full pipeline
python -m research_trend_bot.main              # uses config.yaml
python -m research_trend_bot.main config.yaml  # explicit path

# Run weekly feedback summarization
python -m research_trend_bot.feedback_cli config.yaml

# Propose an interests.yaml update from feedback (opens a PR + emails)
python -m research_trend_bot.interests_cli config.yaml

# Install in dev mode
pip install -e .
```

## Project structure

```
src/research_trend_bot/
  main.py          # Pipeline orchestrator (fetch → score → analyze → email)
  config.py        # YAML config loader + env secret helpers
  models.py        # Pydantic models (config + pipeline data)
  fetcher.py       # Top-level fetch orchestrator: arxiv + HF merge/dedupe, adaptive days_back
  hf_fetcher.py    # Hugging Face daily_papers source (maps HF items → ArxivPaper via arxiv_id)
  scorer.py        # Stage 1: batch abstract scoring via Gemini (with keyword pre-filter)
  analyzer.py      # Stage 2: full PDF analysis via Gemini
  email_builder.py # Jinja2 HTML + plain-text email builder
  sender.py        # SMTP email sender
  feedback.py      # GitHub Issue-based feedback: load, format, summarize, URL builder
  feedback_cli.py  # CLI entry point for biweekly feedback summarization
  interests_tuner.py # Propose interests.yaml updates from feedback; render YAML, open PR, build email
  interests_cli.py # CLI entry point for the weekly interests-tuning step (PR + email)
  templates/
    newsletter.html # Jinja2 email template (packaged with the module)
  prompts/
    scoring.py     # Scoring system/user prompts
    analysis.py    # Analysis system/user prompts
    feedback_summary.py  # Feedback summarization prompt
    interests_tuning.py  # Interests-update proposal prompt
interests.yaml                               # Tracked research interests (git history for changes)
config.yaml                                  # Email, LLM, feedback settings (gitignored)
feedback_summary.json                        # LLM-generated feedback summary (auto-updated)
.github/ISSUE_TEMPLATE/
  feedback_positive.yml                      # Issue Form: thumbs-up with reason dropdown
  feedback_negative.yml                      # Issue Form: thumbs-down with reason dropdown
  feedback_reference.yml                     # Issue Form: paste arXiv links of exemplar papers
.github/workflows/daily_digest.yml           # GitHub Actions cron (weekdays KST 11:00 / UTC 02:00)
.github/workflows/feedback_summary.yml       # Weekly feedback summary + interests-tuning PR (Mon UTC 03:00)
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

- **Interests**: `interests.yaml` (git-tracked) — research interests, filtering, language, days_back, special_instructions. Changes are tracked via git history.
- **Config**: `config.yaml` (gitignored) — email, llm, feedback settings.
- `load_config()` merges both files: `interests.yaml` fields override `config.yaml` when present.
- **Secrets**: `.env` file with `GEMINI_API_KEY`, `SMTP_PASSWORD`, and optionally `GITHUB_TOKEN`
- Two levels of `special_instructions`: per-interest and global
- `language`: `"ko"` (Korean, default) or `"en"`
- `days_back` auto-expands up to 7 if no papers found (e.g., weekends)
- **Feedback** (opt-in): `feedback.enabled: true` + `feedback.github_repo: "owner/repo"` — collects thumbs up/down via GitHub Issues; disabled by default with zero impact on existing behavior
- Feedback URLs include `interest` param to record which interests were active when feedback was given
- `feedback_summary.json` includes `active_interests` list for context tracking

## Testing rules

- **Minimize API usage**: This project uses a free-tier Gemini API key with strict rate limits (20 RPM). When running tests, keep API calls to the bare minimum — just enough to verify things work. Use `top_k: 1` or similar to reduce analysis calls.
- **Always use a virtual environment**: Never install dependencies directly into the system Python. Use `venv` or equivalent before installing anything.

## Important notes

- Papers come from two sources: arxiv category search + HF `daily_papers`. They're merged and deduped by `arxiv_id` in `fetcher.py::_merge_and_dedupe`; when a paper appears in both, `source` is set to `"both"` and the arxiv entry (richer metadata) is kept.
- HF items without an arxiv-formatted ID are skipped in `hf_fetcher.py` — the analyzer needs an arxiv PDF URL. HF fetch failures are swallowed (logged) so the pipeline can continue with arxiv-only results.
- Scorer uses keyword pre-filter before LLM calls to save API quota
- Scorer batch size is 25 (not 10) — optimized for free-tier rate limits
- Scorer has built-in 429 retry that respects Gemini's `retryDelay`
- Analyzer passes raw PDF bytes to Gemini via `Part.from_bytes()` — PDFs >20 MB or >30 pages are skipped
- `PdfReader` requires `io.BytesIO()` wrapper around raw bytes
- `interests.yaml` is git-tracked; `config.yaml` is gitignored. `load_config()` merges both (interests.yaml wins).
- The `email_builder.py` `bulletize` Jinja2 filter converts `"- "` prefixed lines to `<ul><li>` HTML
- Feedback system is fully opt-in (`feedback.enabled: false` by default) — when disabled, `feedback_context=""` is passed through scorer/analyzer with no prompt changes and no email buttons rendered
- Feedback uses GitHub Issue Form templates (`.github/ISSUE_TEMPLATE/feedback_*.yml`) with reason dropdown; `build_feedback_urls()` generates `?template=...&paper=...` query params
- **Reference papers**: a global "Suggest reference papers" email button (`build_reference_url()`) opens `feedback_reference.yml` where the user pastes arXiv links. The `reference-paper` label must exist in the repo for the Issue Form to apply it — GitHub silently drops template labels that don't exist. `load_reference_papers()` fetches all open issues (`_fetch_open_issues`) and keeps reference submissions via `_is_reference_issue()`, which matches the `reference-paper` label OR the Issue Form body marker `### Paper links` (fallback so a missing label can't silently drop a submission). It extracts arxiv IDs (`_extract_arxiv_ids`), enriches them with title/abstract via the arxiv API (`_references_from_issues`), and `format_reference_context()` injects them into scorer/analyzer prompts as the strongest positive signal (boost ~3 points; capped at `MAX_REFERENCE_PAPERS=20`). Reference context is concatenated into `feedback_context`, so it shares the same enabled-gating
- Reference papers also feed the weekly summary: `summarize_and_cleanup()` folds them into `build_summary_prompt(reference_text=...)` and closes reference issues older than 30 days alongside feedback issues (`feedback_summary.json` records `total_references`). Closing an old reference issue drops its direct scorer boost (loader reads open issues only), but its gist persists via the summary
- Reference papers DO influence the scorer keyword pre-filter: `_reference_keywords()` derives specific title tokens (>=4 chars, minus `_TITLE_STOPWORDS`) and passes them to `_keyword_prefilter(extra_keywords=...)` so reference-similar candidates survive even without an interest-keyword match (at the cost of more LLM calls)
- `_parse_issue_body()` supports both Issue Form format (`### Label\n\nValue`) and legacy `**Key**: value` format
- `feedback_summary.json` is committed to repo and auto-updated by the weekly workflow
- **Interests auto-tuning**: after the weekly summary, `interests_cli.py` runs `interests_tuner.propose_interests_update()` — feeds the learned summary + recent thumbs feedback + reference papers to the LLM (structured `InterestsProposal` output) for a CONSERVATIVE update to the interest list (may add/remove interests, keywords, categories, special_instructions). Changes are applied ONLY as a reviewable PR (`open_interests_pr()` via the GitHub REST API — needs `pull-requests: write` and the repo's "Allow Actions to create PRs" setting) and a notification email (`build_proposal_email()`), never committed to main directly. `interests_changed()` compares order-insensitively so keyword/interest reordering never opens a PR; `summarize_changes()` produces a reviewable semantic diff (the raw YAML diff re-serializes the whole file since `safe_dump` drops the hand-formatted quotes/comments). `render_interests_yaml()` replaces only the `research_interests` block and preserves other top-level keys (filtering, language, days_back)
