"""
core/config.py — Centralised configuration with environment variable support.
All secrets come from environment variables. Never hardcoded.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AppConfig:
    # ── LLM ──────────────────────────────────────────────────
    anthropic_api_key: str  = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    gemini_api_key: str     = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    groq_api_key: str       = field(default_factory=lambda: os.environ.get("GROQ_API_KEY", ""))
    openrouter_api_key: str = field(default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", ""))
    default_provider: str   = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "Claude (Anthropic)"))
    claude_model: str       = field(default_factory=lambda: os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"))
    ollama_url: str         = field(default_factory=lambda: os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str       = field(default_factory=lambda: os.environ.get("OLLAMA_MODEL", "llama3"))
    groq_model: str         = field(default_factory=lambda: os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"))
    openrouter_model: str   = field(default_factory=lambda: os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"))
    llm_max_tokens: int     = field(default_factory=lambda: int(os.environ.get("LLM_MAX_TOKENS", "4096")))
    llm_timeout_sec: int    = field(default_factory=lambda: int(os.environ.get("LLM_TIMEOUT_SEC", "120")))

    # ── Database ─────────────────────────────────────────────
    db_path: str            = field(default_factory=lambda: os.environ.get("DB_PATH", "data/req2defect.db"))

    # ── Auth ─────────────────────────────────────────────────
    auth_enabled: bool      = field(default_factory=lambda: os.environ.get("AUTH_ENABLED", "false").lower() == "true")
    auth_password_hash: str = field(default_factory=lambda: os.environ.get("AUTH_PASSWORD_HASH", ""))

    # ── App ──────────────────────────────────────────────────
    app_title: str          = field(default_factory=lambda: os.environ.get("APP_TITLE", "Req2Defect"))
    app_env: str            = field(default_factory=lambda: os.environ.get("APP_ENV", "development"))
    # mock_mode removed — all runs go live
    log_level: str          = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))

    # ── External integrations ────────────────────────────────
    jira_url: str           = field(default_factory=lambda: os.environ.get("JIRA_URL", ""))
    jira_email: str         = field(default_factory=lambda: os.environ.get("JIRA_EMAIL", ""))
    jira_api_token: str     = field(default_factory=lambda: os.environ.get("JIRA_API_TOKEN", ""))
    jira_project_key: str   = field(default_factory=lambda: os.environ.get("JIRA_PROJECT_KEY", "PROJ"))

    # ── Execution ────────────────────────────────────────────
    playwright_headless: bool = field(default_factory=lambda: os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() == "true")
    test_base_url: str        = field(default_factory=lambda: os.environ.get("TEST_BASE_URL", "http://localhost:3000"))
    appium_url: str           = field(default_factory=lambda: os.environ.get("APPIUM_URL", "http://localhost:4723"))

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def has_llm_key(self) -> bool:
        return bool(
            self.anthropic_api_key or self.gemini_api_key
            or self.groq_api_key or self.openrouter_api_key
        )

    def validate(self) -> list[str]:
        """Return list of validation warnings (non-fatal)."""
        warnings = []
        if not self.has_llm_key:
            warnings.append(
                "No LLM API key set. Pipeline will run in mock mode only. "
                "Free options: Groq (console.groq.com) or OpenRouter (openrouter.ai)."
            )
        if self.is_production and not self.auth_enabled:
            warnings.append("AUTH_ENABLED=false in production environment.")
        if self.jira_url and not self.jira_api_token:
            warnings.append("JIRA_URL set but JIRA_API_TOKEN is missing.")
        return warnings


# Singleton
_config: Optional[AppConfig] = None

def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()
    return _config
