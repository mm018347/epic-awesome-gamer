# -*- coding: utf-8 -*-
import os
from pathlib import Path
from hcaptcha_challenger.agent import AgentConfig
from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent
VOLUMES_DIR = PROJECT_ROOT.joinpath("volumes")
LOG_DIR = VOLUMES_DIR.joinpath("logs")
USER_DATA_DIR = VOLUMES_DIR.joinpath("user_data")
HCAPTCHA_DIR = VOLUMES_DIR.joinpath("hcaptcha")

class EpicSettings(AgentConfig):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # === [关键修改：AiHubMix 中转配置] ===
    # 填入 AiHubMix 的 sk- 令牌
    GEMINI_API_KEY: str | None = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY"))
    # 中转地址 (注意：部分 SDK 需要去掉末尾的 /v1，这里我们先按标准填)
    GEMINI_BASE_URL: str = Field(default=os.getenv("GEMINI_BASE_URL", "https://aihubmix.com/v1"))
    # 2025年12月后 Gemini 2.5 Pro 已移出免费层级
    GEMINI_MODEL: str = Field(default=os.getenv("GEMINI_MODEL", "gemini-2.5-pro"))

    # === [原有配置保持不变] ===
    EPIC_EMAIL: str = Field(default_factory=lambda: os.getenv("EPIC_EMAIL"))
    EPIC_PASSWORD: SecretStr = Field(default_factory=lambda: os.getenv("EPIC_PASSWORD"))
    DISABLE_BEZIER_TRAJECTORY: bool = Field(default=True)
    cache_dir: Path = HCAPTCHA_DIR.joinpath(".cache")
    challenge_dir: Path = HCAPTCHA_DIR.joinpath(".challenge")
    captcha_response_dir: Path = HCAPTCHA_DIR.joinpath(".captcha")
    ENABLE_APSCHEDULER: bool = Field(default=True)
    TASK_TIMEOUT_SECONDS: int = Field(default=900)
    REDIS_URL: str = Field(default="redis://redis:6379/0")
    CELERY_WORKER_CONCURRENCY: int = Field(default=1)
    CELERY_TASK_TIME_LIMIT: int = Field(default=1200)
    CELERY_TASK_SOFT_TIME_LIMIT: int = Field(default=900)

    @property
    def user_data_dir(self) -> Path:
        target_ = USER_DATA_DIR.joinpath(self.EPIC_EMAIL)
        target_.mkdir(parents=True, exist_ok=True)
        return target_

settings = EpicSettings()
# 忽略掉一些不兼容的验证码提示
settings.ignore_request_questions = ["Please drag the crossing to complete the lines"]
