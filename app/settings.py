# -*- coding: utf-8 -*-
"""
Epic Kiosk 配置模組
支持 SiliconFlow / OpenAI 相容格式 API
"""
import os
import re
import sys
import asyncio
import base64
import json
import random
from pathlib import Path
from typing import Any, List, Union

# === 引入所需庫 ===
from hcaptcha_challenger.agent import AgentConfig
from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict
from loguru import logger

# --- 核心路徑定義 ---
PROJECT_ROOT = Path(__file__).parent
VOLUMES_DIR = PROJECT_ROOT.joinpath("volumes")
LOG_DIR = VOLUMES_DIR.joinpath("logs")
USER_DATA_DIR = VOLUMES_DIR.joinpath("user_data")
RUNTIME_DIR = VOLUMES_DIR.joinpath("runtime")
RECORD_DIR = VOLUMES_DIR.joinpath("record")

# ==========================================
# API 提供商配置
# ==========================================
# 預設使用 SiliconFlow；保留 API_PROVIDER 僅用於日誌和部署標識。
API_PROVIDER = os.getenv("API_PROVIDER", "siliconflow")

# === 配置類定義 ===
class EpicSettings(AgentConfig):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # [基礎配置] OpenAI 相容 API Key
    # 新部署使用 API_KEY；相容舊版 SILICONFLOW_API_KEY，避免已有 .env 直接失效。

    API_KEY: SecretStr | None = Field(
        default_factory=lambda: os.getenv("API_KEY") or os.getenv("SILICONFLOW_API_KEY"),
        description="相容 OpenAI 的 API 金鑰",
    )

    # 覆蓋父類的 GEMINI_API_KEY，使其變為可選（本項目透過相容層調用模型）
    GEMINI_API_KEY: SecretStr | None = Field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY", "not_used"),
        description="Gemini API Key（本專案無需設定）",
    )

    # API 基礎地址；新部署使用 API_BASE_URL；相容舊版 SILICONFLOW_BASE_URL。
    API_BASE_URL: str = Field(
        default_factory=lambda: os.getenv("API_BASE_URL") or os.getenv("SILICONFLOW_BASE_URL", "https://openrouter.ai/api/v1"),
        description="相容 OpenAI 的 API 基礎網址",
    )

    # === 全局統一模型配置 ===
    # 相容舊配置（GEMINI_MODEL 作為預設）
    GEMINI_MODEL: str = Field(
        default=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        description="預設模型名稱",
    )

    # === 驗證碼模型（需要視覺能力）===
    CAPTCHA_MODEL: str = Field(
        default=os.getenv("CAPTCHA_MODEL", "Qwen/Qwen3-VL-32B-Instruct"),
        description="驗證碼識別模型（主力）",
    )
    CAPTCHA_MODEL_FALLBACK: str = Field(
        default=os.getenv("CAPTCHA_MODEL_FALLBACK", "Qwen/Qwen3-VL-30B-A3B-Instruct"),
        description="驗證碼識別模型（備用）",
    )

    # === 主力模型（一般文本任務）===
    PRIMARY_MODEL: str = Field(
        default=os.getenv("PRIMARY_MODEL", "deepseek-ai/DeepSeek-V4-Flash"),
        description="主力文本模型",
    )
    PRIMARY_MODEL_FALLBACK: str = Field(
        default=os.getenv("PRIMARY_MODEL_FALLBACK", "deepseek-ai/DeepSeek-V4-Pro"),
        description="主力文本模型（備用）",
    )

    # === hcaptcha-challenger 內建模型配置（必須覆蓋預設值）===
    # 這些屬性會覆蓋 AgentConfig 的預設 gemini 模型名稱
    CHALLENGE_CLASSIFIER_MODEL: str = Field(
        default=os.getenv("CAPTCHA_MODEL", "Qwen/Qwen3-VL-32B-Instruct"),
        description="挑戰分類模型",
    )
    IMAGE_CLASSIFIER_MODEL: str = Field(
        default=os.getenv("CAPTCHA_MODEL", "Qwen/Qwen3-VL-32B-Instruct"),
        description="圖像分類模型 (image_label_binary)",
    )
    SPATIAL_POINT_REASONER_MODEL: str = Field(
        default=os.getenv("CAPTCHA_MODEL", "Qwen/Qwen3-VL-32B-Instruct"),
        description="空間點推理模型 (image_label_area_select)",
    )
    SPATIAL_PATH_REASONER_MODEL: str = Field(
        default=os.getenv("CAPTCHA_MODEL", "Qwen/Qwen3-VL-32B-Instruct"),
        description="空間路徑推理模型 (image_drag_drop)",
    )

    EPIC_EMAIL: str = Field(default_factory=lambda: os.getenv("EPIC_EMAIL", ""))
    EPIC_PASSWORD: SecretStr = Field(
        default_factory=lambda: SecretStr(os.getenv("EPIC_PASSWORD", ""))
    )
    DISABLE_BEZIER_TRAJECTORY: bool = Field(default=True)

    # === hcaptcha-challenger 超時配置 ===
    # 單次驗證碼處理總超時（秒）
    EXECUTION_TIMEOUT: float = Field(
        default=float(os.getenv("HCAPTCHA_EXECUTION_TIMEOUT", "180")),
        description="驗證碼處理總超時時間（秒）",
    )

    # 驗證碼響應超時（秒）
    RESPONSE_TIMEOUT: float = Field(
        default=float(os.getenv("HCAPTCHA_RESPONSE_TIMEOUT", "90")),
        description="驗證碼響應超時時間（秒）"
    )

    # Epic 結帳頁的 hCaptcha iframe 渲染有抖動，預設 1.5s 偏短。
    WAIT_FOR_CHALLENGE_VIEW_TO_RENDER_MS: int = Field(
        default=int(os.getenv("HCAPTCHA_RENDER_WAIT_MS", "3000")),
        description="等待驗證碼視圖渲染的時間（毫秒）",
    )

    ignore_request_questions: list[str] = Field(
        default_factory=lambda: [
            item.strip()
            for item in os.getenv(
                "HCAPTCHA_IGNORE_QUESTIONS",
                "",
            ).split("||")
            if item.strip()
        ],
        description="點擊 hCaptcha 題目來重新整理，而不是在那邊浪費時間解題",
    )
    RETRY_ON_FAILURE: bool = Field(default=True)
    enable_challenger_debug: bool = Field(
        default=os.getenv("HCAPTCHA_DEBUG", "true").lower() in {"1", "true", "yes", "on"}
    )

    CAPTCHA_PROVIDER: str = Field(
        default=os.getenv("CAPTCHA_PROVIDER", "none").lower(),
        description="驗證碼服務商 fallback：none / 2captcha",
    )
    CAPTCHA_PROVIDER_API_KEY: SecretStr = Field(
        default_factory=lambda: SecretStr(os.getenv("CAPTCHA_PROVIDER_API_KEY", "")),
        description="驗證碼服務商 API Key",
    )
    CAPTCHA_PROVIDER_SITE_KEY: str = Field(
        default=os.getenv("CAPTCHA_PROVIDER_SITE_KEY", "91e4137f-95af-4bc9-97af-cdcedce21c8c"),
        description="Epic 登入頁 hCaptcha sitekey",
    )
    CAPTCHA_PROVIDER_TIMEOUT: int = Field(
        default=int(os.getenv("CAPTCHA_PROVIDER_TIMEOUT", "180")),
        description="驗證碼服務商等待超時（秒）",
    )
    CAPTCHA_PROVIDER_POLL_INTERVAL: int = Field(
        default=int(os.getenv("CAPTCHA_PROVIDER_POLL_INTERVAL", "5")),
        description="驗證碼服務商輪詢間隔（秒）",
    )

    # 禁用 hcaptcha 文件保存（使用 /tmp 臨時目錄）
    cache_dir: Path = Path("/tmp/hcaptcha/.cache")
    challenge_dir: Path = Path("/tmp/hcaptcha/.challenge")
    captcha_response_dir: Path = Path("/tmp/hcaptcha/.captcha")

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

# 記錄當前配置
logger.info(f"🎯 API 提供商: {API_PROVIDER}")
logger.info(f"🔐 驗證碼模型: {settings.CAPTCHA_MODEL} (備用: {settings.CAPTCHA_MODEL_FALLBACK})")
logger.info(f"🤖 主力模型: {settings.PRIMARY_MODEL} (備用: {settings.PRIMARY_MODEL_FALLBACK})")

# ==========================================
# OpenAI 相容 API 補丁
# 注意：部分視覺模型不支援 response_format: json_object
# 解決方案：從響應中提取 JSON 代碼塊
# ==========================================
def _apply_openai_compatible_patch():
    """
    OpenAI 相容 API 調用層。

    預設使用 SiliconFlow：
    - Base URL: https://api.siliconflow.cn/v1
    - API Key 獲取地址: https://cloud.siliconflow.cn/i/OVI2n57p
    - 視覺和文本模型均透過 /v1/chat/completions 調用
    """
    if not settings.API_KEY:
        logger.warning("⚠️ 未配置 API_KEY")
        return

    try:
        from google import genai
        from google.genai import types
        import httpx

        # 獲取 API Key
        if hasattr(settings.API_KEY, 'get_secret_value'):
            api_key = settings.API_KEY.get_secret_value()
        else:
            api_key = str(settings.API_KEY)

        base_url = settings.API_BASE_URL.rstrip('/')
        if base_url.endswith('/v1'):
            base_url = base_url[:-3]

        logger.info(f"🚀 OpenAI 相容補丁載入中... | 地址: {base_url}")

        # ==========================================
        # 輔助函數：將 Gemini contents 轉換為 OpenAI messages
        # ==========================================
        def _convert_gemini_to_openai(contents: List, model: str) -> tuple:
            """
            將 Gemini 格式的 contents 轉換為 OpenAI 格式的 messages
            返回: (messages, has_images)
            """
            messages = []
            has_images = False

            for content in contents:
                # 處理字串類型（簡單的文本消息）
                if isinstance(content, str):
                    if content.strip():
                        messages.append({"role": "user", "content": content})

                # 處理 Gemini Content 對象
                elif hasattr(content, 'parts'):
                    text_parts = []
                    image_parts = []

                    for part in content.parts:
                        # 處理文本
                        if hasattr(part, 'text') and part.text:
                            text_parts.append(part.text)

                        # 處理內聯圖片 (inline_data)
                        if hasattr(part, 'inline_data') and part.inline_data:
                            has_images = True
                            blob = part.inline_data
                            if hasattr(blob, 'data'):
                                if isinstance(blob.data, bytes):
                                    img_data = blob.data
                                else:
                                    img_data = bytes(blob.data)

                                mime_type = getattr(blob, 'mime_type', 'image/png') or 'image/png'
                                b64_data = base64.b64encode(img_data).decode('utf-8')
                                data_url = f"data:{mime_type};base64,{b64_data}"

                                image_parts.append({
                                    "type": "image_url",
                                    "image_url": {"url": data_url}
                                })

                        # 處理 file_data（來自 upload 的文件）
                        if hasattr(part, 'file_data') and part.file_data:
                            has_images = True

                    # 構建 OpenAI 消息格式
                    if text_parts or image_parts:
                        msg_content = []
                        if text_parts:
                            combined_text = "\n".join(text_parts)
                            msg_content.append({"type": "text", "text": combined_text})
                        msg_content.extend(image_parts)
                        messages.append({
                            "role": "user",
                            "content": msg_content if len(msg_content) > 1 else (msg_content[0] if msg_content else "")
                        })

                elif hasattr(content, 'role') and hasattr(content, 'parts'):
                    role = 'assistant' if content.role == 'model' else content.role
                    text = " ".join([p.text for p in content.parts if hasattr(p, 'text')])
                    if text:
                        messages.append({"role": role, "content": text})

            return messages, has_images

        # ==========================================
        # 輔助函數：從響應文本中提取 JSON
        # ==========================================
        def _extract_json_from_response(response_text: str, response_schema=None):
            """
            從模型響應中提取 JSON（支援多種格式）

            嘗試順序：
            1. 直接解析整個響應
            2. 提取 ```json 代碼塊
            3. 提取 ``` 代碼塊
            4. 提取 { } 範圍內的內容
            """
            if not response_text:
                return None

            # 方法 1：直接解析
            try:
                json_data = json.loads(response_text.strip())
                if response_schema:
                    return response_schema(**json_data)
                return json_data
            except (json.JSONDecodeError, Exception):
                pass

            # 方法 2：提取 ```json 代碼塊
            json_match = re.search(r'```json\s*([\s\S]*?)```', response_text)
            if json_match:
                try:
                    json_data = json.loads(json_match.group(1).strip())
                    if response_schema:
                        return response_schema(**json_data)
                    return json_data
                except (json.JSONDecodeError, Exception):
                    pass

            # 方法 3：提取 ``` 代碼塊（無語言標記）
            code_match = re.search(r'```\s*([\s\S]*?)```', response_text)
            if code_match:
                try:
                    json_data = json.loads(code_match.group(1).strip())
                    if response_schema:
                        return response_schema(**json_data)
                    return json_data
                except (json.JSONDecodeError, Exception):
                    pass

            # 方法 4：提取 { } 範圍內的內容
            brace_match = re.search(r'\{[\s\S]*\}', response_text)
            if brace_match:
                try:
                    json_data = json.loads(brace_match.group(0))
                    if response_schema:
                        return response_schema(**json_data)
                    return json_data
                except (json.JSONDecodeError, Exception):
                    pass

            return None

        # ==========================================
        # 輔助函數：調用 OpenAI API（不使用 JSON mode）
        # ==========================================
        async def _call_openai_api(
            model: str,
            messages: List[dict],
            temperature: float = 0.7,
            max_tokens: int = 4096,
            response_schema=None,
            system_instruction: str = None,
        ) -> Any:
            """
            調用 OpenAI 相容 API
            注意：不使用 response_format，因為部分視覺模型不支援
            """
            request_base_url = base_url
            use_opencode_free = str(model).endswith("-free")
            if use_opencode_free:
                request_base_url = os.getenv("OPENCODE_BASE_URL", "https://opencode.ai/zen").rstrip("/")
            url = f"{request_base_url}/v1/chat/completions"

            headers = {"Content-Type": "application/json"}
            if not use_opencode_free and os.getenv("OPENCODE_NO_AUTH", "").lower() not in {"1", "true", "yes", "on"}:
                headers["Authorization"] = f"Bearer {api_key}"

            # 構建消息列表
            final_messages = []

            # 添加 system instruction
            if system_instruction:
                final_messages.append({"role": "system", "content": system_instruction})

            # 如果有 response_schema，在 system 消息中添加格式要求
            if response_schema:
                schema_json = response_schema.model_json_schema()
                schema_str = json.dumps(schema_json, indent=2, ensure_ascii=False)
                spatial_instruction = ""
                if getattr(response_schema, "__name__", "") == "ImageAreaSelectChallenge":
                    spatial_instruction = """
座標任務額外要求：
- 使用圖片上標註的 X/Y 座標軸讀數，不要使用圖片像素尺寸。
- 座標必須落在目標物體的中心區域，不要點擊邊緣、空白、座標軸或標題。
- 先比較所有候選目標，再估算目標完整包圍框的左、右、上、下邊界。
- 返回包圍框的算術中心；花朵任務應點擊花瓣匯聚的中心核心，絕不點擊花瓣尖端。"""
                elif getattr(response_schema, "__name__", "") == "ImageDragDropChallenge":
                    spatial_instruction = """
拖放座標規則：
- 僅拖動左側標記為「Move」的動物圖示；起始點必須為左側圖示的中心。
- 終點必須為右側網格中空白格子的中心；絕不拖放到已有動物圖示的格子中。
- 不要將左側欄位視為目標區域。目標通常在左側右方，因此終點 X 座標應明顯大於起始點 X。
- 返回座標時，請使用驗證碼頁面的完整螢幕截圖座標系統，而非裁切後的圖片座標。
- 所有座標必須位於可見的 hCaptcha 面板內。不要返回負數座標或螢幕截圖之外的座標。
- 每個可移動圖示最多使用一條路徑。若需要兩個圖示，請返回兩條路徑。
- 先推斷目標網格的行列規律並定位空白格子，再將相應動物放置在空白格中心。
- 當動物圖示形成四行四列時，將目標視為 4x4 網格。左側欄位在 4x4 網格之外。
- 空白格子是指右側網格中僅有背景的方塊。已有動物的格子不得作為終點。
- 透過填表方式解決：寫出右側網格中所有可見格子的動物類型，空白處標記為 EMPTY，再從重複的行/列規律推斷每個 EMPTY 格子。
- 僅在該格子的推斷動物類型與其中一個可移動圖示匹配時，才選擇該空白格子。
- 規律範例：若完整行規律為 [章魚, 雞, 鴨, 青蛙]，而某行為 [章魚, EMPTY, 鴨, EMPTY]，則第 2 列需要雞，第 4 列需要青蛙。
- 規律範例：若行規律為 [鴨, 熊, 企鵝, 章魚]，而某行為 [鴨, EMPTY, 企鵝, EMPTY]，則第 2 列需要熊，第 4 列需要章魚。
- 若可移動圖示為章魚與企鵝，僅將章魚拖動至行/列需要章魚的 EMPTY 格子，企鵝同理。
- 不要僅因為格子空白就選擇它；它必須同時匹配目標動物並完成規律。"""
                schema_instruction = f"""你必須嚴格按照以下 JSON Schema 格式返迴響應。
返回的 JSON 必須包含在 ```json 代碼塊中。
不要輸出分析過程、思考過程、解釋文字或 Markdown 標題；只返回一個 ```json 代碼塊。

JSON Schema:
```json
{schema_str}
```

重要：請確保返回有效的 JSON 格式，包含在代碼塊中。
{spatial_instruction}"""
                if final_messages and final_messages[0].get("role") == "system":
                    final_messages[0]["content"] += "\n\n" + schema_instruction
                else:
                    final_messages.insert(0, {"role": "system", "content": schema_instruction})

            final_messages.extend(messages)

            payload = {
                "model": model,
                "messages": final_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                # 注意：不使用 response_format，部分視覺模型不支援
            }

            retryable_statuses = {429, 500, 502, 503, 504}
            last_error = None
            is_structured_captcha = response_schema is not None
            api_timeout = float(os.getenv("CAPTCHA_API_TIMEOUT", "45")) if is_structured_captcha else 120.0
            max_attempts = int(os.getenv("CAPTCHA_API_RETRIES", "1")) if is_structured_captcha else 3

            async with httpx.AsyncClient(timeout=api_timeout) as client:
                for attempt in range(1, max_attempts + 1):
                    try:
                        response = await client.post(url, headers=headers, json=payload)
                        if response.status_code == 200:
                            return response.json()

                        error_msg = f"API 調用失敗: {response.status_code} - {response.text}"
                        last_error = Exception(error_msg)
                        logger.error(f"❌ {error_msg}")
                        if response.status_code not in retryable_statuses or attempt == max_attempts:
                            raise last_error
                    except httpx.HTTPError as exc:
                        last_error = exc
                        logger.error(f"❌ API 網路異常: {exc}")
                        if attempt == max_attempts:
                            raise

                    delay = min(2 ** (attempt - 1), 4) + random.uniform(0, 0.5)
                    logger.warning(f"⏳ API 調用失敗，{delay:.1f}s 後重試 [{attempt}/{max_attempts}]")
                    await asyncio.sleep(delay)

            raise last_error or RuntimeError("API 調用失敗")

        # ==========================================
        # 劫持 Client 初始化
        # ==========================================
        orig_init = genai.Client.__init__
        def new_init(self, *args, **kwargs):
            kwargs['api_key'] = api_key
            kwargs['http_options'] = types.HttpOptions(base_url="https://generativelanguage.googleapis.com")
            current_model = kwargs.get('model', settings.GEMINI_MODEL)
            logger.info(f"🚀 OpenAI 相容補丁已應用 | 模型: {current_model}")
            orig_init(self, *args, **kwargs)

        genai.Client.__init__ = new_init

        # ==========================================
        # 劫持文件上傳（儲存到記憶體快取）
        # ==========================================
        file_cache = {}

        # ==========================================
        # 驗證碼模型切換機制
        # ==========================================
        # 跟蹤驗證碼調用狀態，實現智慧模型切換
        captcha_call_state = {
            'call_count': 0,          # 當前會話驗證碼調用次數
            'last_call_time': 0,      # 上次調用時間戳
            'use_fallback': False,    # 是否應該使用備用模型
            'success_count': 0,       # 成功次數
            'failure_count': 0,       # 失敗次數（透過調用頻率推斷）
        }
        CAPTCHA_FAILURE_THRESHOLD = int(os.getenv("CAPTCHA_FALLBACK_AFTER_CALLS", "0"))
        CAPTCHA_TIME_WINDOW = int(os.getenv("CAPTCHA_FALLBACK_TIME_WINDOW", "300"))

        async def patched_upload(self_files, file, **kwargs):
            """將文件內容儲存到記憶體快取，返回偽造的文件 ID"""
            if hasattr(file, 'read'):
                content = file.read()
                if asyncio.iscoroutine(content):
                    content = await content
            elif isinstance(file, (str, Path)):
                with open(file, 'rb') as f:
                    content = f.read()
            else:
                content = bytes(file)

            if asyncio.iscoroutine(content):
                content = await content

            file_id = f"sf_{id(content)}_{len(content)}"
            file_cache[file_id] = content
            pass  # 文件快取日誌已移除
            return types.File(name=file_id, uri=file_id, mime_type="image/png")

        genai.files.AsyncFiles.upload = patched_upload

        # ==========================================
        # 劫持 generate_content：核心轉換邏輯
        # ==========================================
        orig_generate = genai.models.AsyncModels.generate_content

        async def patched_generate(self_models, model, contents, **kwargs):
            """
            將 Gemini API 調用轉換為 OpenAI API 調用
            從響應中提取 JSON 代碼塊
            支援模型自動切換（驗證碼任務 vs 普通任務）
            """
            # 用於跟蹤是否需要使用備用模型
            use_fallback = False

            try:
                # 標準化 contents
                normalized = contents if isinstance(contents, list) else [contents]

                # 檢查是否有快取文件需要處理
                has_cached_files = False
                for content in normalized:
                    if hasattr(content, 'parts'):
                        for part in content.parts:
                            if hasattr(part, 'file_data') and part.file_data:
                                file_uri = getattr(part.file_data, 'file_uri', None) or getattr(part.file_data, 'uri', None)
                                if file_uri and file_uri in file_cache:
                                    has_cached_files = True
                                    data = file_cache[file_uri]
                                    if not hasattr(part, 'inline_data') or part.inline_data is None:
                                        part.inline_data = types.Blob(data=data, mime_type="image/png")
                                    else:
                                        part.inline_data.data = data

                # 轉換為 OpenAI 格式
                messages, has_images = _convert_gemini_to_openai(normalized, model)

                if not messages:
                    raise ValueError("無法從 contents 中提取有效消息")

                # 判斷任務類型並選擇合適的模型
                is_captcha_task = has_images or has_cached_files

                # 獲取當前時間戳
                import time
                current_time = time.time()

                if is_captcha_task:
                    # 檢查是否需要重設計數器（超過時間窗口）
                    if current_time - captcha_call_state['last_call_time'] > CAPTCHA_TIME_WINDOW:
                        captcha_call_state['call_count'] = 0
                        captcha_call_state['use_fallback'] = False
                        logger.debug("🔄 驗證碼計數器已重設（超過時間窗口）")

                    # 更新調用計數
                    captcha_call_state['call_count'] += 1
                    captcha_call_state['last_call_time'] = current_time

                    # 判斷是否應該使用備用模型
                    # 當連續調用次數超過閾值時，切換到備用模型
                    if CAPTCHA_FAILURE_THRESHOLD > 0 and captcha_call_state['call_count'] > CAPTCHA_FAILURE_THRESHOLD:
                        captcha_call_state['use_fallback'] = True
                        logger.info(f"🔄 驗證碼重試次數過多（{captcha_call_state['call_count']}次），切換到備用模型")
                        selected_model = settings.CAPTCHA_MODEL_FALLBACK
                    else:
                        selected_model = settings.CAPTCHA_MODEL

                    logger.debug(f"🎯 驗證碼調用 #{captcha_call_state['call_count']} | 模型: {selected_model}")
                else:
                    selected_model = settings.PRIMARY_MODEL

                logger.debug(f"🤖 調用 OpenAI 相容 API | 模型: {selected_model} | 圖片: {is_captcha_task}")

                # 提取配置參數
                config = kwargs.get('config', {})
                temperature = getattr(config, 'temperature', 0.7) if hasattr(config, 'temperature') else 0.7
                max_tokens = getattr(config, 'max_output_tokens', 4096) if hasattr(config, 'max_output_tokens') else 4096

                # 提取 response_schema（結構化輸出）
                response_schema = None
                if hasattr(config, 'response_schema'):
                    response_schema = config.response_schema
                    logger.debug(f"📋 檢測到 response_schema: {response_schema.__name__ if hasattr(response_schema, '__name__') else response_schema}")
                    max_tokens = min(max_tokens if isinstance(max_tokens, int) else 4096, int(os.getenv("CAPTCHA_RESPONSE_MAX_TOKENS", "1200")))
                    temperature = min(temperature if isinstance(temperature, (int, float)) else 0.7, 0.2)

                # 提取 system_instruction
                system_instruction = None
                if hasattr(config, 'system_instruction'):
                    if hasattr(config.system_instruction, 'parts'):
                        for part in config.system_instruction.parts:
                            if hasattr(part, 'text'):
                                system_instruction = part.text
                                break

                # 調用 OpenAI API
                result = await _call_openai_api(
                    model=selected_model,
                    messages=messages,
                    temperature=temperature if isinstance(temperature, (int, float)) else 0.7,
                    max_tokens=max_tokens if isinstance(max_tokens, int) else 4096,
                    response_schema=response_schema,
                    system_instruction=system_instruction,
                )

                # 提取響應文本
                message = result.get('choices', [{}])[0].get('message', {})
                response_text = message.get('content') or message.get('reasoning') or ''
                if not response_text and message.get('reasoning_details'):
                    response_text = "\n".join(
                        str(item.get('text', ''))
                        for item in message.get('reasoning_details', [])
                        if isinstance(item, dict)
                    )
                logger.debug(f"📄 原始響應: {repr(response_text[:300])}")

                # 處理結構化輸出
                parsed_response = None
                if response_schema and response_text:
                    parsed_response = _extract_json_from_response(response_text, response_schema)
                    if parsed_response:
                        logger.debug(f"✅ JSON 解析成功")
                    else:
                        logger.debug(f"⚠️ JSON 解析失敗，返回原始文本")

                # 構建 Gemini 格式的響應
                response = types.GenerateContentResponse(
                    candidates=[
                        types.Candidate(
                            content=types.Content(
                                parts=[types.Part(text=response_text)],
                                role='model'
                            ),
                            finish_reason='STOP'
                        )
                    ]
                )

                # 如果有解析好的結構化響應，設置 parsed 屬性
                if parsed_response:
                    response.parsed = parsed_response

                return response

            except Exception as e:
                error_str = str(e)
                logger.error(f"❌ API 調用異常: {error_str}")

                # 嘗試使用備用模型重試
                if is_captcha_task:
                    fallback_model = settings.CAPTCHA_MODEL_FALLBACK
                    logger.debug(f"⚠️ 嘗試使用備用驗證碼模型: {fallback_model}")
                else:
                    fallback_model = settings.PRIMARY_MODEL_FALLBACK
                    logger.debug(f"⚠️ 嘗試使用備用主力模型: {fallback_model}")

                # 重試一次
                try:
                    result = await _call_openai_api(
                        model=fallback_model,
                        messages=messages,
                        temperature=temperature if isinstance(temperature, (int, float)) else 0.7,
                        max_tokens=max_tokens if isinstance(max_tokens, int) else 4096,
                        response_schema=response_schema,
                        system_instruction=system_instruction,
                    )

                    message = result.get('choices', [{}])[0].get('message', {})
                    response_text = message.get('content') or message.get('reasoning') or ''
                    if not response_text and message.get('reasoning_details'):
                        response_text = "\n".join(
                            str(item.get('text', ''))
                            for item in message.get('reasoning_details', [])
                            if isinstance(item, dict)
                        )
                    logger.debug(f"📄 備用模型響應: {repr(response_text[:300])}")

                    # 處理結構化輸出
                    parsed_response = None
                    if response_schema and response_text:
                        parsed_response = _extract_json_from_response(response_text, response_schema)
                        if parsed_response:
                            logger.debug(f"✅ 備用模型 JSON 解析成功")

                    response = types.GenerateContentResponse(
                        candidates=[
                            types.Candidate(
                                content=types.Content(
                                    parts=[types.Part(text=response_text)],
                                    role='model'
                                ),
                                finish_reason='STOP'
                            )
                        ]
                    )

                    if parsed_response:
                        response.parsed = parsed_response

                    return response

                except Exception as fallback_error:
                    logger.error(f"❌ 備用模型也失敗: {fallback_error}")
                    raise

        genai.models.AsyncModels.generate_content = patched_generate
        logger.info("✅ OpenAI 相容補丁載入成功")

    except Exception as e:
        logger.error(f"❌ 嚴重：OpenAI 相容補丁載入失敗! 原因: {e}")
        import traceback
        traceback.print_exc()

# ==========================================
# 載入 OpenAI 相容補丁
# ==========================================
_apply_openai_compatible_patch()

# 導出
__all__ = ['settings']",
