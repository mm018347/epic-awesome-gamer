# -*- coding: utf-8 -*-
import os
import sys
import asyncio
from pathlib import Path

# === 引入所需库 ===
from hcaptcha_challenger.agent import AgentConfig
from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict
from loguru import logger

# --- 核心路径定义 ---
PROJECT_ROOT = Path(__file__).parent
VOLUMES_DIR = PROJECT_ROOT.joinpath("volumes")
LOG_DIR = VOLUMES_DIR.joinpath("logs")
USER_DATA_DIR = VOLUMES_DIR.joinpath("user_data")
RUNTIME_DIR = VOLUMES_DIR.joinpath("runtime")
SCREENSHOTS_DIR = VOLUMES_DIR.joinpath("screenshots")
RECORD_DIR = VOLUMES_DIR.joinpath("record")
HCAPTCHA_DIR = VOLUMES_DIR.joinpath("hcaptcha")

# === 配置类定义 ===
class EpicSettings(AgentConfig):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # [基础配置] AiHubMix 必须使用 SecretStr 类型
    GEMINI_API_KEY: SecretStr | None = Field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY"),
        description="AiHubMix 的令牌",
    )
    
    GEMINI_BASE_URL: str = Field(
        default=os.getenv("GEMINI_BASE_URL", "https://aihubmix.com"),
        description="中转地址",
    )
    
    GEMINI_MODEL: str = Field(
        default=os.getenv("GEMINI_MODEL", "gemini-2.5-pro"),
        description="模型名稱（統一設定所有模型）",
    )

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
settings.ignore_request_questions = ["Please drag the crossing to complete the lines"]

# 用 GEMINI_MODEL 統一覆寫 hcaptcha-challenger 的所有模型設定
settings.CHALLENGE_CLASSIFIER_MODEL = settings.GEMINI_MODEL
settings.IMAGE_CLASSIFIER_MODEL = settings.GEMINI_MODEL
settings.SPATIAL_POINT_REASONER_MODEL = settings.GEMINI_MODEL
settings.SPATIAL_PATH_REASONER_MODEL = settings.GEMINI_MODEL

# ==========================================
# [方案一修复版] AiHubMix 终极补丁
# ==========================================
def _apply_aihubmix_patch():
    if not settings.GEMINI_API_KEY:
        return

    try:
        from google import genai
        from google.genai import types
        
        # 1. 劫持 Client 初始化 (自动修正中转路径)
        orig_init = genai.Client.__init__
        def new_init(self, *args, **kwargs):
            if hasattr(settings.GEMINI_API_KEY, 'get_secret_value'):
                api_key = settings.GEMINI_API_KEY.get_secret_value()
            else:
                api_key = str(settings.GEMINI_API_KEY)
            
            kwargs['api_key'] = api_key
            
            base_url = settings.GEMINI_BASE_URL.rstrip('/')
            # 不再強制加入 /gemini 字尾，直接使用使用者設定的 URL
            
            kwargs['http_options'] = types.HttpOptions(base_url=base_url)
            logger.info(f"🚀 AiHubMix 补丁已应用 | 模型: {settings.GEMINI_MODEL} | 地址: {base_url}")
            orig_init(self, *args, **kwargs)
        
        genai.Client.__init__ = new_init

        # 2. 劫持文件上传 (绕过 400/403 错误，并修复 TypeError)
        try:
            file_cache = {}

            # 自定义 helper，避免依赖 google 内部库
            def _local_to_list(c):
                return c if isinstance(c, list) else [c]

            async def patched_upload(self_files, file, **kwargs):
                if hasattr(file, 'read'): content = file.read()
                elif isinstance(file, (str, Path)):
                    with open(file, 'rb') as f: content = f.read()
                else: content = bytes(file)
                
                if asyncio.iscoroutine(content): content = await content
                
                # 伪造文件上传，实际只存内存
                file_id = f"bypass_{id(content)}"
                file_cache[file_id] = content
                return types.File(name=file_id, uri=file_id, mime_type="image/png")

            orig_generate = genai.models.AsyncModels.generate_content
            async def patched_generate(self_models, model, contents, **kwargs):
                normalized = _local_to_list(contents)
                
                for content in normalized:
                    if hasattr(content, 'parts'):
                        for i, part in enumerate(content.parts):
                            # 如果发现是我们伪造的文件 ID，立马替换成 Base64
                            if part.file_data and part.file_data.file_uri in file_cache:
                                data = file_cache[part.file_data.file_uri]
                                content.parts[i] = types.Part.from_bytes(data=data, mime_type="image/png")
                
                # [核心修复点] 强制使用关键字参数 model= 和 contents=
                # 这解决了 "takes 1 positional argument but 3 were given" 的报错
                return await orig_generate(self_models, model=model, contents=normalized, **kwargs)

            genai.files.AsyncFiles.upload = patched_upload
            genai.models.AsyncModels.generate_content = patched_generate
            logger.info("🚀 Base64 文件绕过补丁加载成功 (参数兼容版)")
            
        except Exception as ie:
            logger.warning(f"⚠️ 文件绕过补丁依然失败: {ie}")

    except Exception as e:
        logger.error(f"❌ 严重：AiHubMix 补丁加载完全失败! 原因: {e}")

# 执行补丁
_apply_aihubmix_patch()
