"""Configuration loading.

Two layers:
  * environment (secrets, switches)  -> `Env`
  * YAML config (profile, searches, timing) -> `Config`

`load()` returns a `Settings` bundle used everywhere else in the package.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- env


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default)).expanduser()


@dataclass(frozen=True)
class Env:
    anthropic_api_key: str | None
    model: str
    data_dir: Path
    config_path: Path
    answers_path: Path
    resume_dir: Path
    dry_run: bool
    require_review: bool
    email_mode: str
    web_host: str
    web_port: int
    gmail_credentials: Path
    gmail_token: Path

    @classmethod
    def load(cls) -> Env:
        base = REPO_ROOT
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            model=os.getenv("JOBAPPLY_MODEL", "claude-opus-5"),
            data_dir=_env_path("JOBAPPLY_DATA_DIR", str(base / "data")),
            config_path=_env_path("JOBAPPLY_CONFIG", str(base / "config" / "config.yaml")),
            answers_path=_env_path("JOBAPPLY_ANSWERS", str(base / "config" / "answers.yaml")),
            resume_dir=_env_path("JOBAPPLY_RESUME_DIR", str(base / "resumes")),
            dry_run=_env_bool("JOBAPPLY_DRY_RUN", True),
            require_review=_env_bool("JOBAPPLY_REQUIRE_REVIEW", True),
            email_mode=os.getenv("JOBAPPLY_EMAIL_MODE", "draft"),
            web_host=os.getenv("JOBAPPLY_WEB_HOST", "127.0.0.1"),
            web_port=int(os.getenv("JOBAPPLY_WEB_PORT", "8000")),
            gmail_credentials=_env_path("JOBAPPLY_GMAIL_CREDENTIALS", str(base / "secrets" / "credentials.json")),
            gmail_token=_env_path("JOBAPPLY_GMAIL_TOKEN", str(base / "secrets" / "token.json")),
        )


# ------------------------------------------------------------------------ config


class SearchSpec(BaseModel):
    name: str
    keywords: str
    location: str = ""
    remote: bool = False
    posted_within_hours: int = 72
    experience_levels: list[str] = Field(default_factory=list)
    easy_apply_only: bool = False
    max_results: int = 25


class MatchingConfig(BaseModel):
    min_score: int = 60
    auto_apply_score: int = 78
    use_llm_rerank: bool = True
    tailor_above_score: int = 70
    max_tailored_per_day: int = 12
    exclude_title_terms: list[str] = Field(default_factory=list)
    exclude_companies: list[str] = Field(default_factory=list)
    require_terms: list[str] = Field(default_factory=list)


class TimingConfig(BaseModel):
    timezone_default: str = "Asia/Kolkata"
    send_window_local: list[str] = Field(default_factory=lambda: ["08:30", "11:00"])
    fallback_window_local: list[str] = Field(default_factory=lambda: ["14:00", "16:30"])
    preferred_weekdays: list[int] = Field(default_factory=lambda: [1, 2, 3])
    allowed_weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    ideal_freshness_hours: int = 24
    max_freshness_hours: int = 168
    immediate_if_fresher_than_hours: int = 6
    max_applications_per_day: int = 15
    max_applications_per_hour: int = 4
    min_minutes_between_applications: int = 7
    jitter_minutes: int = 9


class ApplyConfig(BaseModel):
    enable_linkedin_easy_apply: bool = True
    enable_external_ats: bool = True
    enable_email_channel: bool = True
    ats_vendors: list[str] = Field(default_factory=lambda: ["workday", "greenhouse", "lever", "ashby", "generic"])
    write_cover_letter: bool = True
    screenshot_evidence: bool = True


class ManualConfig(BaseModel):
    inbox_dir: str = "./inbox/screenshots"
    processed_dir: str = "./processed"
    fallback_recipient: str | None = None
    cc: list[str] = Field(default_factory=list)
    signature: str = ""


class Profile(BaseModel):
    model_config = {"extra": "allow"}

    full_name: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    postal_code: str = ""
    address_line1: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""
    headline: str = ""
    years_experience: int = 0
    current_company: str = ""
    current_title: str = ""
    notice_period_days: int = 30
    requires_sponsorship: bool = False
    willing_to_relocate: bool = True
    desired_salary: str = ""
    desired_salary_number: int | None = None
    salary_currency: str = "INR"
    earliest_start_date: str = "+30d"
    gender: str = "Decline to self identify"
    ethnicity: str = "Decline to self identify"
    veteran_status: str = "I am not a protected veteran"
    disability_status: str = "I do not wish to answer"
    pitch: str = ""
    work_authorization: dict[str, str] = Field(default_factory=dict)


class Config(BaseModel):
    profile: Profile = Field(default_factory=Profile)
    searches: list[SearchSpec] = Field(default_factory=list)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    timing: TimingConfig = Field(default_factory=TimingConfig)
    apply: ApplyConfig = Field(default_factory=ApplyConfig)
    manual: ManualConfig = Field(default_factory=ManualConfig)


class AnswerRule(BaseModel):
    match: list[str]
    value: str


class AnswerBank(BaseModel):
    answers: list[AnswerRule] = Field(default_factory=list)
    never_auto_answer: list[str] = Field(default_factory=list)


@dataclass
class Settings:
    env: Env
    config: Config
    answers: AnswerBank
    raw_config: dict[str, Any] = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.env.data_dir / "jobapply.db"

    @property
    def artifacts_dir(self) -> Path:
        return self.env.data_dir / "artifacts"

    @property
    def generated_dir(self) -> Path:
        return self.env.data_dir / "generated"

    @property
    def browser_dir(self) -> Path:
        return self.env.data_dir / "browser"

    def ensure_dirs(self) -> None:
        for p in (self.env.data_dir, self.artifacts_dir, self.generated_dir, self.browser_dir):
            p.mkdir(parents=True, exist_ok=True)


def _read_yaml(path: Path, example_suffix: str = ".example.yaml") -> dict[str, Any]:
    """Read `path`, transparently falling back to the committed example file.

    This keeps a fresh clone runnable (and the test suite green) before the user
    has copied the examples across.
    """
    if not path.exists():
        example = path.with_name(path.name.replace(".yaml", "") + example_suffix)
        if example.exists():
            path = example
        else:
            return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load(reload: bool = False) -> Settings:
    if reload:
        _load_cached.cache_clear()
    return _load_cached()


@lru_cache(maxsize=1)
def _load_cached() -> Settings:
    env = Env.load()
    raw = _read_yaml(env.config_path)
    answers_raw = _read_yaml(env.answers_path)
    settings = Settings(
        env=env,
        config=Config.model_validate(raw),
        answers=AnswerBank.model_validate(answers_raw),
        raw_config=raw,
    )
    settings.ensure_dirs()
    return settings
