"""Pydantic data models for the research trend bot."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ── Config Models ──────────────────────────────────────────────


class ResearchInterest(BaseModel):
    """A single research interest with keywords and arxiv categories."""

    name: str
    keywords: list[str]
    arxiv_categories: list[str] = Field(
        description="e.g. ['cs.CL', 'cs.AI']"
    )
    special_instructions: str | None = None


class FilteringConfig(BaseModel):
    """Configuration for the two-stage filtering pipeline."""

    score_threshold: int = Field(default=6, ge=1, le=10)
    top_k: int = Field(default=5, ge=1)
    max_papers_per_interest: int = Field(default=50, ge=1)


class EmailConfig(BaseModel):
    """SMTP email configuration."""

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    sender_address: str
    sender_name: str = "Research Trend Bot"
    recipients: list[str]


class LLMConfig(BaseModel):
    """LLM model configuration."""

    scoring_model: str = "gemini-2.5-flash-lite"
    analysis_model: str = "gemini-2.5-flash"


class AppConfig(BaseModel):
    """Top-level application configuration."""

    research_interests: list[ResearchInterest]
    filtering: FilteringConfig = FilteringConfig()
    email: EmailConfig
    llm: LLMConfig = LLMConfig()
    language: str = Field(default="ko", description="Output language: 'ko' or 'en'")
    days_back: int = Field(default=1, ge=1, description="How many days back to search")
    special_instructions: str | None = None


# ── Pipeline Data Models ───────────────────────────────────────


class ArxivPaper(BaseModel):
    """Metadata for a paper fetched from arxiv."""

    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    published: datetime
    updated: datetime
    pdf_url: str
    abs_url: str


class RelevanceScore(BaseModel):
    """Stage 1 result: abstract relevance scoring."""

    arxiv_id: str
    score: int = Field(ge=1, le=10)
    reasoning: str
    matched_keywords: list[str]


class ScoredPaper(BaseModel):
    """A paper with its relevance score attached."""

    paper: ArxivPaper
    relevance: RelevanceScore


class PaperAnalysis(BaseModel):
    """Stage 2 result: full paper analysis."""

    arxiv_id: str
    title: str
    authors: list[str]
    affiliations: list[str] = Field(
        default_factory=list,
        description="Institutional affiliations of the authors",
    )
    keywords: list[str]
    task: str = Field(description="What task/problem does this paper address?")
    problem_and_motivation: str
    core_idea: str
    method: str
    experiments_and_results: str
    limitations: str
    personal_relevance: str = Field(
        description="Why this paper matters to the user's research interests"
    )


class AnalyzedPaper(BaseModel):
    """A scored paper with its full analysis."""

    paper: ArxivPaper
    relevance: RelevanceScore
    analysis: PaperAnalysis


class DigestReport(BaseModel):
    """Final report ready for email rendering."""

    generated_at: datetime
    research_interests: list[ResearchInterest]
    total_fetched: int
    total_scored: int
    papers: list[AnalyzedPaper]
