# Research Trend Bot

Automated daily digest of research papers tailored to your interests. Fetches recent papers from **arXiv** and **Hugging Face daily_papers** (merged + deduped), scores relevance with Gemini, analyzes top papers from their full PDFs, and delivers a structured email newsletter.

## Pipeline Flow

```
                       GitHub Issues (opt-in)
                              │
                     ┌────────┴────────┐
                     │ Load Feedback   │
                     │ (recent + summary)│
                     └────────┬────────┘
                              │ feedback_context
                              ▼
arxiv + HF         Gemini Flash-Lite         Gemini Flash           SMTP
   │                     │                       │                   │
   ▼                     ▼                       ▼                   ▼
┌────────┐  papers  ┌──────────┐  scored  ┌───────────┐  report  ┌───────┐
│ Fetch  │────────▶│  Score   │────────▶│ Analyze   │────────▶│ Email │
│ Papers │         │ Abstracts│         │ Full PDFs │         │ Build │──▶ Send
└────────┘         └──────────┘         └───────────┘         └───────┘
     │                  │                                         │
     │  merge + dedupe  │  keyword pre-filter               👍/👎 buttons
     │  by arxiv_id     │  + batch scoring (25/batch)       (Issue Form w/ reason dropdown)
     │  adaptive        │  + 429 retry w/ backoff
     │  days_back
     │  (1→3→5→7)
```

### Stage Details

| Stage | Model | Input | Output |
|-------|-------|-------|--------|
| **Fetch** | — | arxiv categories + HF `daily_papers` + date range | `ArxivPaper` list, deduped by `arxiv_id` (overlap tagged `source="both"`) |
| **Pre-filter** | — | keyword matching on title + abstract | reduced paper list (~30% drop) |
| **Score** | `gemini-2.5-flash-lite` | abstracts in batches of 25 | relevance scores 1-10 |
| **Analyze** | `gemini-2.5-flash` | full PDF per paper | structured analysis (bullet-point) |
| **Email** | — | Jinja2 HTML template | HTML + plain-text email |

## Features

- **Dual paper sources** — arxiv category search + Hugging Face `daily_papers` (community-curated). Merged and deduped by `arxiv_id`; overlaps are tagged `source="both"` and shown with a badge in the email
- **Two-stage LLM pipeline** — fast scoring to filter, then deep PDF analysis on top papers only
- **Keyword pre-filter** — local substring matching before LLM calls to save API quota
- **Adaptive date window** — auto-expands `days_back` (1 → 3 → 5 → 7) when no papers found (e.g., weekends)
- **Feedback loop** — opt-in thumbs up/down buttons in emails collect feedback via GitHub Issue Form templates with structured reason dropdowns; recent feedback and LLM-generated summaries are injected into scoring/analysis prompts to improve future recommendations
- **Special instructions** — per-interest and global free-text instructions injected into LLM prompts
- **Multi-language** — output in Korean (`ko`, default) or English (`en`), with technical terms kept in English
- **Bullet-point format** — analysis results use concise bullet points, rendered as `<ul>` in email
- **Rate-limit resilience** — 429 retry with server-suggested `retryDelay` backoff
- **GitHub Actions** — daily digest (weekdays KST 11:00) + biweekly feedback summary (1st & 15th)

## Setup

### 1. Clone and install

```bash
git clone <repo-url>
cd research-trend-bot
pip install -e .
```

### 2. Configure

Create two YAML files in the repo root (see [Configuration](#configuration) below for the schema):

- **`interests.yaml`** — research interests, filtering, language, days_back, special_instructions. Commit this file to track changes over time.
- **`config.yaml`** — email, LLM, feedback settings (gitignored; may reference secrets).

When both files exist, `interests.yaml` fields take precedence.

### 3. Set secrets

Create a `.env` file:

```
GEMINI_API_KEY=your-gemini-api-key
SMTP_PASSWORD=your-gmail-app-password
GITHUB_TOKEN=your-github-token    # optional, only needed if feedback is enabled
```

### 4. Run

```bash
python -m research_trend_bot.main
```

## Configuration

**`interests.yaml`** (git-tracked):

```yaml
research_interests:
  - name: "Video Generation × 3D Vision"
    keywords: [video generation, video diffusion, NeRF, ...]
    arxiv_categories: [cs.CV, cs.GR]
    special_instructions: "Focus on aspects applicable to 3D motion generation."

  - name: "LLM & Multimodal LLM"
    keywords: [large language model, LLM, vision-language model, ...]
    arxiv_categories: [cs.CL, cs.CV, cs.AI]

filtering:
  score_threshold: 7   # 1-10, papers below this are discarded
  top_k: 5             # max papers for full analysis
  max_papers_per_interest: 50

language: ko           # ko | en
days_back: 1           # auto-expands up to 7 if no papers found

# Global instruction applied to all scoring/analysis
special_instructions: "Prioritize papers from reputable institutions."
```

**`config.yaml`** (gitignored):

```yaml
email:
  smtp_host: "smtp.gmail.com"
  smtp_port: 587
  sender_address: "you@gmail.com"
  recipients: ["you@gmail.com"]

llm:
  scoring_model: "gemini-2.5-flash-lite"
  analysis_model: "gemini-2.5-flash"

# Optional: GitHub Issue-based feedback (disabled by default)
feedback:
  enabled: true
  github_repo: "owner/repo"
  github_token_env: "GITHUB_TOKEN"

# Optional: Hugging Face daily_papers source (enabled by default)
huggingface:
  enabled: true
  limit: 100       # max HF papers to consider per run
  max_pages: 10    # pagination safety guard
```

### Key options

| Option | Description | Default |
|--------|-------------|---------|
| `score_threshold` | Minimum relevance score (1-10) | `6` |
| `top_k` | Max papers to fully analyze | `5` |
| `language` | Output language (`ko` or `en`) | `ko` |
| `days_back` | Initial search window in days | `1` |
| `special_instructions` | Free-text instructions for LLM (global or per-interest) | `None` |
| `feedback.enabled` | Enable GitHub Issue feedback buttons in email | `false` |
| `feedback.github_repo` | GitHub repo for feedback issues (`"owner/repo"`) | `""` |
| `feedback.github_token_env` | Env var name for GitHub token | `"GITHUB_TOKEN"` |
| `huggingface.enabled` | Fetch from Hugging Face `daily_papers` in addition to arXiv | `true` |
| `huggingface.limit` | Max HF papers to consider per run | `100` |
| `huggingface.max_pages` | HF API pagination safety guard | `10` |

## GitHub Actions

Two workflows are included:

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| `daily_digest.yml` | Weekdays KST 11:00 (UTC 02:00) | Run the full digest pipeline |
| `feedback_summary.yml` | 1st & 15th of each month (UTC 03:00) | Summarize feedback + close old issues |

**Required secrets**: `GEMINI_API_KEY`, `SMTP_PASSWORD`, `CONFIG_YAML` (email/llm/feedback settings only — interests are read from `interests.yaml` in the repo)

**Optional secrets**: `GITHUB_TOKEN` (needed only if feedback is enabled)

## License

MIT
