# -*- coding: utf-8 -*-
"""
@Time    : 2025/7/16 22:13
@Author  : QIN2DIM
@GitHub  : https://github.com/QIN2DIM
@Desc    :
"""
import asyncio
import json
import time
from contextlib import suppress
from enum import Enum

from hcaptcha_challenger.agent import AgentV
from loguru import logger
from playwright.async_api import expect, Page, Response

from settings import RUNTIME_DIR, settings
from services.captcha import HCaptchaChallengerSolver
from services.captcha.solver import CaptchaSolveStatus, TwoCaptchaTokenSolver, inject_hcaptcha_token

URL_CLAIM = "https://store.epicgames.com/en-US/free-games"
LOGIN_FAST_RESULT_TIMEOUT = 15
LOGIN_AFTER_CAPTCHA_TIMEOUT = 60
LOGIN_AFTER_RESUBMIT_TIMEOUT = 45


class ErrorType(Enum):
    """
    錯誤類型列舉，用於精細化區分不同錯誤，便於前端展示不同提示

    設計思路：
    - 每種錯誤類型對應不同的使用者操作建議
    - 前端根據錯誤類型展示不同的彈出視窗內容
    - 便於日誌分析和問題排查
    """
    # 成功，無錯誤
    SUCCESS = "success"

    # 帳號或密碼錯誤 - 需要使用者檢查密碼重新提交
    INVALID_CREDENTIALS = "invalid_credentials"

    # 帳號被鎖定 - 需要使用者聯繫 Epic 客服
    ACCOUNT_LOCKED = "account_locked"

    # EULA 協議處理失敗 - 需要使用者手動登入 Epic 接受協議
    EULA_FAILED = "eula_failed"

    # 驗證碼識別失敗/超時 - 建議使用者稍後重試
    CAPTCHA_FAILED = "captcha_failed"

    # 驗證碼需要人工處理 - hCaptcha 動物拖曳題自動識別不穩定
    CAPTCHA_MANUAL_REQUIRED = "captcha_manual_required"

    # 登入超時 - 可能是網路問題，建議稍後重試
    LOGIN_TIMEOUT = "login_timeout"

    # 網路超時 - Epic 服務不可達
    NETWORK_TIMEOUT = "network_timeout"

    # Cookie 無效 - 需要重新登入
    COOKIE_INVALID = "cookie_invalid"

    # 未知錯誤 - 需要使用者查看日誌
    UNKNOWN = "unknown"


class LoginFailedException(Exception):
    """
    登入失敗異常

    攜帶錯誤類型資訊，便於上層呼叫者判斷具體失敗原因
    """
    def __init__(self, error_type: ErrorType, message: str = ""):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


class EpicAuthorization:

    def __init__(self, page: Page):
        self.page = page

        self._is_login_success_signal = asyncio.Queue()
        self._is_refresh_csrf_signal = asyncio.Queue()
        self._login_error_code = None  # 儲存登入錯誤碼

    async def _save_login_debug(self, reason: str):
        """儲存登入失敗現場，便於排查 Epic/hCaptcha 頁面變化。"""
        safe_reason = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in reason) or "unknown"
        debug_dir = RUNTIME_DIR.joinpath("login_debug")
        with suppress(Exception):
            debug_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())

        with suppress(Exception):
            screenshot_path = debug_dir.joinpath(f"{timestamp}_{safe_reason}.png")
            await self.page.screenshot(path=str(screenshot_path), full_page=True)
            logger.warning(f"🧾 已儲存登入失敗截圖: {screenshot_path}")

        with suppress(Exception):
            html_path = debug_dir.joinpath(f"{timestamp}_{safe_reason}.html")
            html_path.write_text(await self.page.content(), encoding="utf-8")
            logger.warning(f"🧾 已儲存登入失敗 HTML: {html_path}")

        with suppress(Exception):
            frames_path = debug_dir.joinpath(f"{timestamp}_{safe_reason}_frames.json")
            frames = [
                {"index": idx, "name": frame.name, "url": frame.url}
                for idx, frame in enumerate(self.page.frames)
            ]
            frames_path.write_text(json.dumps(frames, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.warning(f"🧾 已儲存登入 frame 列表: {frames_path}")

    async def _has_visible_hcaptcha_challenge(self, timeout_ms: int = 1000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            for frame in self.page.frames:
                url = frame.url or ""
                if "hcaptcha.com" not in url or "frame=challenge" not in url:
                    continue
                with suppress(Exception):
                    if await frame.locator("div.challenge-view").first.is_visible(timeout=500):
                        return True
                with suppress(Exception):
                    if await frame.locator("div.challenge-view").count() > 0:
                        return True
            await self.page.wait_for_timeout(250)
        return False

    async def _click_hcaptcha_checkbox(self) -> bool:
        """點擊 hCaptcha checkbox，顯式觸發 challenge/getcaptcha。"""
        checkbox_selectors = [
            "#checkbox",
            "div#checkbox",
            "[role='checkbox']",
            ".checkbox",
        ]

        for frame in self.page.frames:
            url = frame.url or ""
            if "hcaptcha.com" not in url or "frame=checkbox" not in url:
                continue
            for selector in checkbox_selectors:
                with suppress(Exception):
                    checkbox = frame.locator(selector).first
                    if not await checkbox.is_visible(timeout=1000):
                        continue
                    await checkbox.click(force=True, timeout=5000)
                    logger.info("✅ 已點擊 hCaptcha checkbox，等待 challenge")
                    return True
        return False

    async def _prepare_hcaptcha_challenge(self, agent: AgentV, timeout_ms: int = 20000) -> None:
        """
        登入頁不會總是自動打開 challenge。

        先點擊 checkbox 觸發 getcaptcha，再交給 AgentV 解題；不要在 challenge
        不可見時直接 refresh_challenge，否則會點擊隱藏重新整理按鈕並報
        "Element is not visible or does not exist"。
        """
        deadline = time.monotonic() + timeout_ms / 1000
        checkbox_clicked = False

        while time.monotonic() < deadline:
            if not agent._captcha_payload_queue.empty():
                logger.info("✅ 已捕獲 hCaptcha payload")
                return
            if await self._has_visible_hcaptcha_challenge(timeout_ms=500):
                logger.info("✅ 已檢測到可見 hCaptcha challenge")
                return
            if not checkbox_clicked:
                checkbox_clicked = await self._click_hcaptcha_checkbox()
            await self.page.wait_for_timeout(1000)

        logger.warning("⚠️ 等待 hCaptcha challenge/payload 超時，繼續交給 AgentV 兜底處理")

    async def _is_animal_pattern_drag_challenge(self) -> bool:
        needle = "put the animal icons into the correct spots to complete the pattern"
        for frame in self.page.frames:
            url = frame.url or ""
            if "hcaptcha.com" not in url or "frame=challenge" not in url:
                continue
            with suppress(Exception):
                content = (await frame.content()).lower()
                if needle in content:
                    return True
        return False

    async def _resubmit_login_if_password_page(self, reason: str) -> bool:
        """
        hCaptcha 處理結束後，Epic 有時會回到密碼頁但不會自動提交。

        這種狀態下繼續判定驗證碼失敗是錯誤的：頁面已經脫離 challenge，
        正確動作是補一次登入提交，然後繼續等待 Epic 登入回調。
        """
        try:
            if await self._has_visible_hcaptcha_challenge(timeout_ms=500):
                logger.debug("Skip password-page resubmit because hCaptcha challenge is still visible")
                return False

            password_input = self.page.locator("#password").first
            sign_in_button = self.page.locator("#sign-in").first

            if not await password_input.is_visible(timeout=1500):
                return False
            if not await sign_in_button.is_visible(timeout=1500):
                return False

            with suppress(Exception):
                await self.page.evaluate(
                    """
                    () => {
                      const button = document.querySelector('#sign-in');
                      if (button) {
                        button.disabled = false;
                        button.removeAttribute('disabled');
                        button.removeAttribute('aria-disabled');
                        button.tabIndex = 0;
                      }
                      const talonOverlay = document.querySelector('#talon_container_login_prod');
                      if (talonOverlay) {
                        talonOverlay.style.display = 'none';
                        talonOverlay.style.visibility = 'hidden';
                      }
                    }
                    """
                )

            with suppress(Exception):
                current_password = await password_input.input_value(timeout=1000)
                if not current_password:
                    await password_input.fill(settings.EPIC_PASSWORD.get_secret_value(), timeout=5000)

            with suppress(Exception):
                await sign_in_button.scroll_into_view_if_needed(timeout=1000)

            try:
                await sign_in_button.click(timeout=5000)
            except Exception:
                await self.page.click("#sign-in", timeout=5000)

            logger.warning(f"Captcha flow returned to password page; resubmitted sign-in ({reason})")
            return True
        except Exception as err:
            logger.debug(f"Password-page resubmit check skipped: {err}")
            return False

    async def _solve_with_provider(self) -> bool:
        provider = (settings.CAPTCHA_PROVIDER or "none").lower()
        if provider in {"", "none", "disabled"}:
            return False
        if provider != "2captcha":
            logger.warning(f"Unsupported captcha provider: {provider}")
            return False

        api_key = settings.CAPTCHA_PROVIDER_API_KEY.get_secret_value()
        solver = TwoCaptchaTokenSolver(
            api_key=api_key,
            site_key=settings.CAPTCHA_PROVIDER_SITE_KEY,
            page_url=self.page.url or "https://www.epicgames.com/id/login",
            timeout_seconds=settings.CAPTCHA_PROVIDER_TIMEOUT,
            poll_interval_seconds=settings.CAPTCHA_PROVIDER_POLL_INTERVAL,
        )
        result = await solver.solve()
        if not result.ok or not result.token:
            logger.warning(f"Captcha provider failed: {result.provider} {result.status} {result.message}")
            return False

        await inject_hcaptcha_token(self.page, result.token)
        logger.success(f"Captcha provider returned token: {result.provider}")
        return True

    async def _on_response_anything(self, r: Response):
        if r.request.method != "POST" or "talon" in r.url:
            return

        with suppress(Exception):
            result = await r.json()
            # result_json = json.dumps(result, indent=2, ensure_ascii=False)

            # 記錄所有 POST 響應的 URL，便於除錯
            logger.debug(f"📡 API 響應: {r.url} | 狀態碼: {r.status}")

            if "/id/api/login" in r.url:
                # 記錄完整的登入 API 響應
                logger.debug(f"🔍 登入 API 完整響應: {json.dumps(result, ensure_ascii=False, indent=2)}")
                if result.get("errorCode"):
                    # 記錄錯誤碼並通知登入失敗
                    self._login_error_code = result.get("errorCode")
                    error_msg = result.get("errorMessage", "未知錯誤")
                    # 記錄完整的錯誤資訊
                    logger.error(f"❌ 登入失敗: errorCode={self._login_error_code}, message={error_msg}")
                    logger.error(f"❌ 完整錯誤響應: {json.dumps(result, ensure_ascii=False)}")
                    # 放入失敗訊號，中斷等待
                    self._is_login_success_signal.put_nowait({"error": True, "code": self._login_error_code, "full_response": result})
                else:
                    # 登入成功，記錄 accountId
                    if result.get("accountId"):
                        logger.success(f"✅ 登入 API 返回成功: accountId={result.get('accountId')}")
            elif "/id/api/analytics" in r.url and result.get("accountId"):
                self._is_login_success_signal.put_nowait(result)
            elif "/account/v2/refresh-csrf" in r.url and result.get("success", False) is True:
                self._is_refresh_csrf_signal.put_nowait(result)

    def _map_login_error(self, error_code: str) -> ErrorType:
        if "invalid_account_credentials" in error_code:
            return ErrorType.INVALID_CREDENTIALS
        if "account_locked" in error_code:
            return ErrorType.ACCOUNT_LOCKED
        if "csrf_token_invalid" in error_code:
            return ErrorType.COOKIE_INVALID
        return ErrorType.UNKNOWN

    async def _handle_right_account_validation(self):
        """
        以下驗證僅會在登入成功後出現
        Returns:

        """
        try:
            await self.page.goto(
                "https://www.epicgames.com/account/personal",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            with suppress(Exception):
                await self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception as exc:
            current_url = self.page.url or ""
            if "epicgames.com/account/personal" in current_url:
                logger.warning(f"Account validation navigation timed out but account page is visible: {exc}")
            else:
                raise

        btn_ids = ["#link-success", "#login-reminder-prompt-setup-tfa-skip", "#yes"]

        # == 帳號長期不登入需要做的額外驗證 == #

        while self._is_refresh_csrf_signal.empty() and btn_ids:
            await self.page.wait_for_timeout(500)
            action_chains = btn_ids.copy()
            for action in action_chains:
                with suppress(Exception):
                    reminder_btn = self.page.locator(action)
                    await expect(reminder_btn).to_be_visible(timeout=1000)
                    await reminder_btn.click(timeout=1000)
                    btn_ids.remove(action)

    async def _confirm_login_state_from_page(self, reason: str) -> bool:
        """
        Epic can finish navigation after hCaptcha without emitting the login API
        response that this flow is waiting for. In that case the account page
        itself is the strongest success signal.
        """
        with suppress(Exception):
            await self.page.wait_for_load_state("networkidle", timeout=10000)

        current_url = self.page.url or ""
        if "epicgames.com/account/personal" in current_url:
            logger.success(f"Epic account page reached after {reason}; treating login as successful")
            with suppress(Exception):
                await asyncio.wait_for(self._handle_right_account_validation(), timeout=60)
            return True

        with suppress(Exception):
            status = await self.page.locator("//egs-navigation").get_attribute("isloggedin", timeout=3000)
            if str(status).lower() == "true":
                logger.success(f"Epic navigation reports logged-in after {reason}")
                return True

        return False

    async def _login(self) -> tuple[bool, ErrorType] | None:
        """
        執行登入流程

        Returns:
            tuple[bool, ErrorType]: (是否成功, 錯誤類型)
            - (True, ErrorType.SUCCESS): 登入成功
            - (False, ErrorType.INVALID_CREDENTIALS): 帳號或密碼錯誤
            - (False, ErrorType.ACCOUNT_LOCKED): 帳號被鎖定
            - (False, ErrorType.CAPTCHA_FAILED): 驗證碼識別失敗
            - (False, ErrorType.CAPTCHA_MANUAL_REQUIRED): 驗證碼需要人工處理
            - (False, ErrorType.LOGIN_TIMEOUT): 登入超時
            - None: 異常情況
        """
        # 重設錯誤碼
        self._login_error_code = None

        # 登入 API 通常會在 15 秒內直接返回。僅在首輪等待未成功時
        # 初始化驗證碼 Agent，避免無驗證碼帳號仍啟動昂貴的 HSW 處理。
        agent: AgentV | None = None
        captcha_task: asyncio.Task | None = None
        result_task: asyncio.Task | None = None

        # {{< SIGN IN PAGE >}}
        logger.debug("Login with Email")

        # 用於記錄驗證碼處理是否成功
        captcha_success = False

        try:
            point_url = "https://www.epicgames.com/account/personal?lang=en-US&productName=egs&sessionInvalidated=true"
            await self.page.goto(point_url, wait_until="domcontentloaded")

            # 1. 使用電子郵件地址登入
            email_input = self.page.locator("#email")
            await email_input.clear()
            await email_input.type(settings.EPIC_EMAIL)

            # 2. 點擊繼續按鈕
            await self.page.click("#continue")

            # 3. 輸入密碼
            password_input = self.page.locator("#password")
            await password_input.clear()
            await password_input.type(settings.EPIC_PASSWORD.get_secret_value())

            # 4. 點擊登入按鈕
            await self.page.click("#sign-in")

            # 先註冊 hCaptcha 響應監聽器，避免 getcaptcha payload 在首輪等待期間遺失。
            agent = AgentV(page=self.page, agent_config=settings)

            # 並行啟動：驗證碼處理 + 登入結果等待
            # 關鍵改進：使用 wait_for 快速檢測密碼錯誤
            async def wait_for_login_result():
                """等待登入結果（成功或失敗）"""
                return await self._is_login_success_signal.get()

            async def handle_captcha():
                """Solve captcha through a provider wrapper.

                The login flow consumes solver status only. This prevents the
                Epic login code from hard-looping inside one high-risk captcha
                session and makes manual/provider fallback explicit.
                """
                nonlocal captcha_success
                try:
                    assert agent is not None
                    await self._prepare_hcaptcha_challenge(agent)

                    original_response_timeout = float(settings.RESPONSE_TIMEOUT)
                    original_execution_timeout = float(settings.EXECUTION_TIMEOUT)
                    if await self._is_animal_pattern_drag_challenge():
                        settings.RESPONSE_TIMEOUT = max(original_response_timeout, 120.0)
                        settings.EXECUTION_TIMEOUT = max(original_execution_timeout, 240.0)
                        logger.warning(
                            f"Detected hCaptcha animal drag pattern; extending timeouts "
                            f"response={settings.RESPONSE_TIMEOUT}s execution={settings.EXECUTION_TIMEOUT}s"
                        )

                    solver = HCaptchaChallengerSolver(agent)
                    try:
                        result = await solver.solve()
                    finally:
                        settings.RESPONSE_TIMEOUT = original_response_timeout
                        settings.EXECUTION_TIMEOUT = original_execution_timeout

                    if result.ok:
                        captcha_success = True
                        logger.success("Captcha solver succeeded")
                        return result

                    provider_ok = await self._solve_with_provider()
                    if provider_ok:
                        captcha_success = True
                        return type("CaptchaProviderResult", (), {"ok": True, "status": CaptchaSolveStatus.SUCCESS, "signal": "provider", "message": ""})()

                    if result.status in {CaptchaSolveStatus.RETRY, CaptchaSolveStatus.TIMEOUT}:
                        logger.warning(f"Captcha requires retry or manual fallback: {result.signal or result.message}")
                        await self._save_login_debug("captcha_manual_required")
                        return result
                    else:
                        logger.warning(f"Captcha solver failed: {result.signal or result.message}")
                        await self._save_login_debug("captcha_failed")
                        return result
                except Exception as e:
                    logger.warning(f"Captcha solver exception: {e}")
                    await self._save_login_debug("captcha_exception")
                    return type("CaptchaExceptionResult", (), {"ok": False, "status": CaptchaSolveStatus.FAILED, "signal": None, "message": str(e)})()

            # 先只等待登入 API；大多數帳號不需要啟動驗證碼 Agent。
            result_task = asyncio.create_task(wait_for_login_result())

            # 第一階段：15秒內快速檢測密碼錯誤
            try:
                done, pending = await asyncio.wait(
                    [result_task],
                    timeout=LOGIN_FAST_RESULT_TIMEOUT,
                    return_when=asyncio.FIRST_COMPLETED
                )

                if result_task in done:
                    result = result_task.result()
                    # 檢查是否是登入失敗訊號
                    if result.get("error"):
                        error_code = result.get("code", "")
                        mapped_error = self._map_login_error(error_code)
                        if mapped_error == ErrorType.INVALID_CREDENTIALS:
                            logger.error("❌ 帳號或密碼錯誤")
                        elif mapped_error == ErrorType.ACCOUNT_LOCKED:
                            logger.error("❌ 帳號已被鎖定")
                        elif mapped_error == ErrorType.COOKIE_INVALID:
                            logger.error("❌ 登入 Cookie/CSRF 已失效，需要清理瀏覽器 profile 後重試")
                        else:
                            logger.error(f"❌ 登入失敗: {error_code}")
                        return (False, mapped_error)

                    # 登入成功（無驗證碼或已透過）
                    if result.get("accountId"):
                        logger.success("✅ 登入成功")
                        await asyncio.wait_for(self._handle_right_account_validation(), timeout=60)
                        logger.success("✅ 帳號驗證成功")
                        return (True, ErrorType.SUCCESS)
            except asyncio.CancelledError:
                pass

            # Second phase: start captcha solver and wait for login or solver result.
            captcha_task = asyncio.create_task(handle_captcha())
            try:
                captcha_login_timeout = (
                    float(settings.EXECUTION_TIMEOUT)
                    + float(settings.RESPONSE_TIMEOUT)
                    + 60
                )
                logger.info(f"Waiting for login or captcha result, timeout: {captcha_login_timeout:.0f}s")
                done, _pending = await asyncio.wait(
                    [result_task, captcha_task],
                    timeout=captcha_login_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if not done:
                    logger.error("Captcha/login wait timed out")
                    await self._save_login_debug("captcha_timeout")
                    return (False, ErrorType.CAPTCHA_FAILED)

                if result_task in done:
                    result = result_task.result()
                elif captcha_task in done:
                    captcha_result = captcha_task.result()
                    if not captcha_result or not captcha_result.ok:
                        resubmitted = await self._resubmit_login_if_password_page(
                            f"captcha_{getattr(captcha_result, 'status', 'unknown')}"
                        )
                        if resubmitted:
                            try:
                                result = await asyncio.wait_for(
                                    result_task,
                                    timeout=LOGIN_AFTER_RESUBMIT_TIMEOUT,
                                )
                            except asyncio.TimeoutError:
                                logger.error("Epic login response timed out after password-page resubmit")
                                await self._save_login_debug("login_timeout_after_resubmit")
                                return (False, ErrorType.CAPTCHA_FAILED)
                        else:
                            logger.error("Captcha solver ended without success; manual verification is required")
                            return (False, ErrorType.CAPTCHA_MANUAL_REQUIRED)
                    else:
                        captcha_success = True
                        try:
                            result = await asyncio.wait_for(
                                result_task,
                                timeout=LOGIN_AFTER_CAPTCHA_TIMEOUT,
                            )
                        except asyncio.TimeoutError:
                            resubmitted = await self._resubmit_login_if_password_page("captcha_success_no_login_response")
                            if not resubmitted:
                                if await self._confirm_login_state_from_page("captcha_success_no_login_response"):
                                    return (True, ErrorType.SUCCESS)
                                logger.error("Captcha passed but Epic login response timed out")
                                await self._save_login_debug("login_timeout_after_captcha")
                                return (False, ErrorType.LOGIN_TIMEOUT)
                            try:
                                result = await asyncio.wait_for(
                                    result_task,
                                    timeout=LOGIN_AFTER_RESUBMIT_TIMEOUT,
                            )
                            except asyncio.TimeoutError:
                                if await self._confirm_login_state_from_page("captcha_success_resubmit"):
                                    return (True, ErrorType.SUCCESS)
                                logger.error("Epic login response timed out after captcha success resubmit")
                                await self._save_login_debug("login_timeout_after_captcha_resubmit")
                                return (False, ErrorType.LOGIN_TIMEOUT)
                else:
                    logger.error("Captcha/login wait ended without a usable task result")
                    await self._save_login_debug("captcha_login_wait_empty")
                    return (False, ErrorType.LOGIN_TIMEOUT)

                if result.get("error"):
                    error_code = result.get("code", "")
                    mapped_error = self._map_login_error(error_code)
                    if mapped_error == ErrorType.INVALID_CREDENTIALS:
                        logger.error("Invalid Epic credentials")
                    elif mapped_error == ErrorType.ACCOUNT_LOCKED:
                        logger.error("Epic account is locked")
                    elif mapped_error == ErrorType.COOKIE_INVALID:
                        logger.error("Epic login Cookie/CSRF is invalid; browser profile reset is required")
                    else:
                        logger.error(f"Epic login failed: {error_code}")
                    return (False, mapped_error)

                logger.success("Epic login succeeded")
                await asyncio.wait_for(self._handle_right_account_validation(), timeout=60)
                logger.success("Epic account validation succeeded")
                return (True, ErrorType.SUCCESS)

            except asyncio.TimeoutError:
                if not captcha_success:
                    logger.error("Captcha solve timed out")
                    await self._save_login_debug("captcha_timeout")
                    return (False, ErrorType.CAPTCHA_FAILED)
                if await self._confirm_login_state_from_page("captcha_success_timeout"):
                    return (True, ErrorType.SUCCESS)
                logger.error("Epic login timed out")
                await self._save_login_debug("login_timeout")
                return (False, ErrorType.LOGIN_TIMEOUT)

        except asyncio.TimeoutError:
            logger.error("❌ 登入超時，請檢查帳號密碼")
            return (False, ErrorType.LOGIN_TIMEOUT)
        except Exception as err:
            logger.warning(f"登入異常: {err}")
            return (False, ErrorType.UNKNOWN)
        finally:
            # 登入階段的 AgentV 監聽器不能洩漏到商品頁，否則會繼續處理
            # 隱藏的 hCaptcha 響應並阻塞後續點擊。
            if captcha_task is not None:
                captcha_task.cancel()
                with suppress(asyncio.CancelledError):
                    await captcha_task
            if result_task is not None and not result_task.done():
                result_task.cancel()
                with suppress(asyncio.CancelledError):
                    await result_task
            if agent is not None:
                with suppress(Exception):
                    self.page.remove_listener("response", agent._task_handler)

    async def _handle_eula_correction(self) -> tuple[bool, ErrorType]:
        """
        處理 EULA 修正頁面

        Epic Games 在某些情況下會將使用者重定向到 EULA 修正頁面：
        - 新註冊帳號首次登入
        - Epic 更新服務條款
        - 帳號長期未登入
        - 帳號在新裝置/地區登入

        頁面特徵（基於實際 HTML）：
        - URL 包含 "correction/eula" 或 "corrective="
        - 接受按鈕: <button id="accept" type="submit" aria-label="接受">接受</button>
        - 拒絕按鈕: <button id="decline" type="button" aria-label="拒絕">拒絕</button>
        - 使用 Material UI 元件 (MuiButton-containedPrimary)

        Returns:
            tuple[bool, ErrorType]: (是否成功, 錯誤類型)
            - (True, SUCCESS): 成功接受 EULA
            - (False, EULA_FAILED): 處理失敗，需要使用者手動操作
            - (False, SUCCESS): 無需處理（不在 EULA 頁面）
        """
        current_url = self.page.url

        # 檢測是否在 EULA 修正頁面
        if "correction/eula" not in current_url and "corrective=" not in current_url:
            return (False, ErrorType.SUCCESS)  # 無需處理

        logger.warning("⚠️ 檢測到 EULA 修正頁面，嘗試自動接受協議...")
        logger.info(f"📋 目前 URL: {current_url}")

        try:
            # ============================================================
            # SPA 頁面需要等待網路完全空閒
            # Material UI 對話框需要額外時間繪製和動畫完成
            # ============================================================
            logger.debug("⏳ 等待 EULA 頁面載入完成...")
            await self.page.wait_for_load_state("networkidle")

            # 等待 React/Material UI 繪製完成（對話框動畫約 225ms）
            await self.page.wait_for_timeout(2000)

            # 等待對話框元素出現（確認頁面已繪製）
            try:
                await self.page.wait_for_selector("#accept", timeout=10000)
                logger.debug("✅ EULA 接受按鈕已繪製")
            except Exception as e:
                logger.warning(f"⚠️ 等待按鈕超時: {e}")

            # ============================================================
            # EULA 接受按鈕選擇器（按優先度排序）
            # 基於實際 HTML 結構: <button id="accept" type="submit" aria-label="接受">
            # ============================================================
            accept_selectors = [
                # === 最精確：通過 ID 選擇（最穩定）===
                "#accept",
                "button#accept",

                # === 通過 aria-label 屬性（多語言支援）===
                "//button[@aria-label='接受']",
                "//button[@aria-label='Accept']",

                # === 通過 type=submit（次優）===
                "//button[@type='submit']",

                # === 透過文字匹配（多語言）===
                "//button[normalize-space(text())='接受']",
                "//button[normalize-space(text())='Accept']",

                # === 通過 Material UI class（備用）===
                "//button[contains(@class, 'MuiButton-containedPrimary')]",
            ]

            # 嘗試點擊接受按鈕
            for i, selector in enumerate(accept_selectors, 1):
                try:
                    logger.debug(f"🔍 嘗試 EULA 選擇器 [{i}/{len(accept_selectors)}]: {selector}")

                    btn = self.page.locator(selector).first

                    # 檢查按鈕是否存在且可見
                    if not await btn.is_visible(timeout=3000):
                        logger.debug(f"按鈕不可見: {selector}")
                        continue

                    btn_text = await btn.text_content()
                    logger.info(f"📋 找到 EULA 接受按鈕: '{btn_text}' | 選擇器: {selector}")

                    # ============================================================
                    # 🔥 關鍵修復：使用多種點擊方式確保成功
                    # 某些情況下 Playwright 的普通點擊會被攔截
                    # ============================================================

                    # 方式1：滾動到按鈕位置，確保可見
                    await btn.scroll_into_view_if_needed()
                    await self.page.wait_for_timeout(500)

                    # 方式2：使用 force=True 繞過可操作性檢查
                    try:
                        await btn.click(force=True, timeout=5000)
                        logger.info("👆 已點擊接受按鈕 (force=True)")
                    except Exception as click_err:
                        logger.warning(f"普通點擊失敗，嘗試 JS 點擊: {click_err}")
                        # 方式3：使用 JavaScript 直接點擊
                        await btn.evaluate("el => el.click()")
                        logger.info("👆 已點擊接受按鈕 (JS evaluate)")

                    # 等待頁面跳轉（增加超時時間到 30 秒）
                    logger.info("⏳ 等待頁面跳轉...")
                    await self.page.wait_for_load_state("networkidle", timeout=30000)

                    # 額外等待，確保重定向完成
                    await self.page.wait_for_timeout(2000)

                    # 驗證是否成功跳轉
                    new_url = self.page.url
                    logger.debug(f"📋 點擊後 URL: {new_url}")

                    if "correction/eula" not in new_url and "corrective=" not in new_url:
                        logger.success("✅ EULA 協議已接受，頁面已跳轉")
                        return (True, ErrorType.SUCCESS)
                    else:
                        logger.warning("⚠️ 點擊後仍在 EULA 頁面，嘗試下一個選擇器")

                except Exception as e:
                    logger.debug(f"EULA 選擇器 '{selector}' 失敗: {e}")
                    continue

            # ============================================================
            # 所有選擇器都失敗，記錄詳細的頁面資訊便於除錯
            # ============================================================
            logger.error("❌ 未能找到 EULA 接受按鈕")
            try:
                # 截圖儲存，便於分析
                screenshot_path = f"/tmp/eula_error_{int(time.time())}.png"
                await self.page.screenshot(path=screenshot_path)
                logger.info(f"📸 EULA 頁面截圖已儲存: {screenshot_path}")

                # 列印頁面 HTML，便於除錯
                page_content = await self.page.content()
                logger.debug(f"📄 EULA 頁面 HTML (前 2000 字元):\n{page_content[:2000]}")
            except Exception as e:
                logger.warning(f"儲存除錯資訊失敗: {e}")

            return (False, ErrorType.EULA_FAILED)

        except Exception as e:
            logger.error(f"❌ 處理 EULA 頁面異常: {e}")
            return (False, ErrorType.EULA_FAILED)

    async def invoke(self) -> ErrorType:
        """
        執行 Epic 登入認證流程

        流程：
        1. 訪問 Epic 免費遊戲頁面
        2. 檢測並處理 EULA 修正頁面
        3. 檢查登入狀態
        4. 如果未登入，執行登入流程
        5. 處理登入後的驗證

        Returns:
            ErrorType: 錯誤類型
            - SUCCESS: 登入成功或已登入
            - 其他錯誤類型: 對應的失敗原因
        """
        self.page.on("response", self._on_response_anything)

        for attempt in range(3):
            logger.info(f"🔄 登入嘗試 [{attempt + 1}/3]")

            try:
                await self.page.goto(URL_CLAIM, wait_until="domcontentloaded")
            except Exception as e:
                logger.warning(f"頁面載入失敗: {e}")
                if "timeout" in str(e).lower():
                    return ErrorType.NETWORK_TIMEOUT
                continue

            # ============================================================
            # 🔥 關鍵修復：等待頁面穩定
            # Epic Games 頁面是 SPA，JS 需要時間執行
            # domcontentloaded 觸發時重定向可能還沒完成
            # ============================================================
            await self.page.wait_for_timeout(3000)  # 等待 3 秒讓 JS 執行完成

            # ============================================================
            # 🔥 EULA 修正頁面檢測與處理
            # 登入後可能被重定向到 EULA 頁面，需要自動接受協議
            # ============================================================
            for eula_attempt in range(3):  # 最多處理 3 次 EULA（通常只需要 1 次）
                current_url = self.page.url
                logger.debug(f"📍 目前頁面 URL: {current_url}")
                if "correction/eula" in current_url or "corrective=" in current_url:
                    logger.warning(f"⚠️ 檢測到修正頁面 (EULA 嘗試 {eula_attempt + 1}/3): {current_url}")

                    success, error_type = await self._handle_eula_correction()

                    if success:
                        # EULA 處理成功後，重新導航到目標頁面
                        await self.page.goto(URL_CLAIM, wait_until="domcontentloaded")
                        await self.page.wait_for_timeout(2000)  # 再次等待穩定
                    else:
                        logger.error(f"❌ EULA 處理失敗: {error_type.value}")
                        return error_type  # 返回具體錯誤類型
                else:
                    break

            # 檢查登入狀態（增加超時處理）
            try:
                status = await self.page.locator("//egs-navigation").get_attribute("isloggedin", timeout=15000)
            except Exception as e:
                # 超時時檢查是否在修正頁面
                current_url = self.page.url
                logger.debug(f"📍 獲取登入狀態超時，目前 URL: {current_url}")
                if "correction" in current_url or "eula" in current_url:
                    logger.error("❌ 仍在修正頁面，無法繼續")
                    return ErrorType.EULA_FAILED
                logger.error(f"❌ 獲取登入狀態超時: {e}")

                # 判斷是網路問題還是其他問題
                if "timeout" in str(e).lower():
                    return ErrorType.NETWORK_TIMEOUT
                return ErrorType.UNKNOWN

            if status == "true":
                logger.success("✅ Epic Games 已登入")
                return ErrorType.SUCCESS

            # 執行登入
            login_result = await self._login()
            if login_result:
                success, error_type = login_result
                if success:
                    return ErrorType.SUCCESS
                # 登入失敗，返回具體錯誤類型
                return error_type

            # login_result 為 None 時繼續下一次嘗試
            logger.warning("⚠️ 登入結果為空，嘗試下一次...")
            continue

        # 所有嘗試都失敗
        logger.error("❌ 所有登入嘗試都失敗")
        return ErrorType.UNKNOWN
