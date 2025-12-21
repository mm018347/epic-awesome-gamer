# -*- coding: utf-8 -*-
"""
@Time    : 2025/7/16 21:15
@Author  : QIN2DIM
@GitHub  : https://github.com/QIN2DIM
@Desc    : 修改版 - 修复路径定义并支持 AiHubMix 中转
"""
import os
from pathlib import Path

from hcaptcha_challenger.agent import AgentConfig
from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

# --- 核心路径定义 (必须保留，否则会报 ImportError) ---
PROJECT_ROOT = Path(__file__).parent
VOLUMES_DIR = PROJECT_ROOT.joinpath("volumes")

LOG_DIR = VOLUMES_DIR.joinpath("logs")
USER_DATA_DIR = VOLUMES_DIR.joinpath("user_data")

RUNTIME_DIR = VOLUMES_DIR.joinpath("runtime")
SCREENSHOTS_DIR = VOLUMES_DIR.joinpath("screenshots")
RECORD_DIR = VOLUMES_DIR.joinpath("record")
HCAPTCHA_DIR = VOLUMES_DIR.joinpath("hcaptcha")


class EpicSettings(AgentConfig):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # === [新增] Gemini AiHubMix 配置区 ===
    GEMINI_API_KEY: str | None = Field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY"),
        description="Gemini API Key，填入 AiHubMix 的 sk- 令牌",
    )

    GEMINI_BASE_URL: str = Field(
        default=os.getenv("GEMINI_BASE_URL", "https://aihubmix.com/v1"),
        description="中转接口地址，默认为 AiHubMix",
    )

    GEMINI_MODEL: str = Field(
        default=os.getenv("GEMINI_MODEL", "gemini-2.5-pro"),
        description="使用的模型名称",
    )
    # ===================================

    EPIC_EMAIL: str = Field(
        default_factory=lambda: os.getenv("EPIC_EMAIL"),
        description="Epic 游戏账号",
    )

    EPIC_PASSWORD: SecretStr = Field(
        default_factory=lambda: os.getenv("EPIC_PASSWORD"),
        description=" Epic 游戏密码",
    )

    DISABLE_BEZIER_TRAJECTORY: bool = Field(
        default=True, description="是否关闭贝塞尔曲线轨迹模拟"
    )

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
        if not target_.is_dir():
            target_.mkdir(parents=True, exist_ok=True)
        return target_


settings = EpicSettings()
settings.ignore_request_questions = ["Please drag the crossing to complete the lines"]
