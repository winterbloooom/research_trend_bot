# Research Trend Bot

Automated daily digest of arxiv papers tailored to your research interests. Fetches recent papers, scores relevance with Gemini, analyzes top papers from their full PDFs, and delivers a structured email newsletter.

## Pipeline Flow

```
arxiv API          Gemini Flash-Lite         Gemini Flash           SMTP
   │                     │                       │                   │
   ▼                     ▼                       ▼                   ▼
┌────────┐  papers  ┌──────────┐  scored  ┌───────────┐  report  ┌───────┐
│ Fetch  │────────▶│  Score   │────────▶│ Analyze   │────────▶│ Email │
│ Papers │         │ Abstracts│         │ Full PDFs │         │ Build │──▶ Send
└────────┘         └──────────┘         └───────────┘         └───────┘
     │                  │
     │  adaptive        │  keyword pre-filter
     │  days_back       │  + batch scoring (25/batch)
     │  (1→3→5→7)       │  + 429 retry w/ backoff
```

### Stage Details

| Stage | Model | Input | Output |
|-------|-------|-------|--------|
| **Fetch** | — | arxiv categories + date range | `ArxivPaper` list (deduplicated) |
| **Pre-filter** | — | keyword matching on title + abstract | reduced paper list (~30% drop) |
| **Score** | `gemini-2.5-flash-lite` | abstracts in batches of 25 | relevance scores 1-10 |
| **Analyze** | `gemini-2.5-flash` | full PDF per paper | structured analysis (bullet-point) |
| **Email** | — | Jinja2 HTML template | HTML + plain-text email |

## Features

- **Two-stage LLM pipeline** — fast scoring to filter, then deep PDF analysis on top papers only
- **Keyword pre-filter** — local substring matching before LLM calls to save API quota
- **Adaptive date window** — auto-expands `days_back` (1 → 3 → 5 → 7) when no papers found (e.g., weekends)
- **Special instructions** — per-interest and global free-text instructions injected into LLM prompts
- **Multi-language** — output in Korean (`ko`, default) or English (`en`), with technical terms kept in English
- **Bullet-point format** — analysis results use concise bullet points, rendered as `<ul>` in email
- **Rate-limit resilience** — 429 retry with server-suggested `retryDelay` backoff
- **GitHub Actions** — scheduled to run weekdays at UTC 08:00

## Setup

### 1. Clone and install

```bash
git clone <repo-url>
cd research-trend-bot
pip install -e .
```

### 2. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your research interests, email settings, and preferences. See [Configuration](#configuration) below.

### 3. Set secrets

Create a `.env` file:

```
GEMINI_API_KEY=your-gemini-api-key
SMTP_PASSWORD=your-gmail-app-password
```

### 4. Run

```bash
python -m research_trend_bot.main
```

## Configuration

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

llm:
  scoring_model: "gemini-2.5-flash-lite"
  analysis_model: "gemini-2.5-flash"

email:
  smtp_host: "smtp.gmail.com"
  smtp_port: 587
  sender_address: "you@gmail.com"
  recipients: ["you@gmail.com"]

language: ko           # ko | en
days_back: 1           # auto-expands up to 7 if no papers found

# Global instruction applied to all scoring/analysis
special_instructions: "Prioritize papers from reputable institutions."
```

### Key options

| Option | Description | Default |
|--------|-------------|---------|
| `score_threshold` | Minimum relevance score (1-10) | `6` |
| `top_k` | Max papers to fully analyze | `5` |
| `language` | Output language (`ko` or `en`) | `ko` |
| `days_back` | Initial search window in days | `1` |
| `special_instructions` | Free-text instructions for LLM (global or per-interest) | `None` |

## GitHub Actions

The included workflow (`.github/workflows/daily_digest.yml`) runs the pipeline on weekdays at UTC 08:00. Add `GEMINI_API_KEY` and `SMTP_PASSWORD` as repository secrets.

## License

MIT
