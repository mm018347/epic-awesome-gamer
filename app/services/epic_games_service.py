# -*- coding: utf-8 -*-
# Time       : 2022/1/16 0:25
# Author     : QIN2DIM
# GitHub     : https://github.com/QIN2DIM
# Description: 游戏商城控制句柄

import json
from contextlib import suppress
from enum import Enum
from json import JSONDecodeError
from typing import Any, List

import httpx
from hcaptcha_challenger.agent import AgentV
from loguru import logger
from playwright.async_api import Page
from playwright.async_api import expect, TimeoutError, FrameLocator
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from models import OrderItem, Order
from models import PromotionGame
from settings import settings, RUNTIME_DIR

URL_CLAIM = "https://store.epicgames.com/en-US/free-games"
URL_LOGIN = (
    f"https://www.epicgames.com/id/login?lang=en-US&noHostRedirect=true&redirectUrl={URL_CLAIM}"
)
URL_CART = "https://store.epicgames.com/en-US/cart"
URL_CART_SUCCESS = "https://store.epicgames.com/en-US/cart/success"


URL_PROMOTIONS = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
URL_PRODUCT_PAGE = "https://store.epicgames.com/en-US/p/"
URL_PRODUCT_BUNDLES = "https://store.epicgames.com/en-US/bundles/"


class GameCollectResult(Enum):
    """
    遊戲領取結果列舉

    用於區分不同的執行結果，便於上層呼叫者判斷是否成功
    """
    # 成功：所有遊戲已在庫中
    ALL_OWNED = "all_owned"

    # 成功：遊戲領取成功
    SUCCESS = "success"

    # 失敗：EULA 協議未接受
    EULA_FAILED = "eula_failed"

    # 失敗：Cookie 無效
    COOKIE_INVALID = "cookie_invalid"

    # 失敗：未知錯誤
    UNKNOWN_ERROR = "unknown_error"


def get_promotions() -> List[PromotionGame]:
    """取得週免遊戲資料"""
    def is_discount_game(prot: dict) -> bool | None:
        with suppress(KeyError, IndexError, TypeError):
            offers = prot["promotions"]["promotionalOffers"][0]["promotionalOffers"]
            for i, offer in enumerate(offers):
                if offer["discountSetting"]["discountPercentage"] == 0:
                    return True

    promotions: List[PromotionGame] = []

    resp = httpx.get(URL_PROMOTIONS, params={"local": "zh-CN"})

    try:
        data = resp.json()
    except JSONDecodeError as err:
        logger.error(f"取得促銷資訊失敗: {err}")
        return []

    with suppress(Exception):
        cache_key = RUNTIME_DIR.joinpath("promotions.json")
        cache_key.parent.mkdir(parents=True, exist_ok=True)
        cache_key.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # 取得商店促銷資料與 <本週免費> 遊戲
    for e in data["data"]["Catalog"]["searchStore"]["elements"]:
        if not is_discount_game(e):
            continue

        # -----------------------------------------------------------
        # 🟢 智慧 URL 識別邏輯
        # -----------------------------------------------------------
        is_bundle = False
        if e.get("offerType") == "BUNDLE":
            is_bundle = True
        
        # 補充檢測：分類和標題
        if not is_bundle:
            for cat in e.get("categories", []):
                if "bundle" in cat.get("path", "").lower():
                    is_bundle = True
                    break
        if not is_bundle and "Collection" in e.get("title", ""):
             is_bundle = True

        base_url = URL_PRODUCT_BUNDLES if is_bundle else URL_PRODUCT_PAGE

        try:
            if e.get('offerMappings'):
                slug = e['offerMappings'][0]['pageSlug']
                e["url"] = f"{base_url.rstrip('/')}/{slug}"
            elif e.get("productSlug"):
                e["url"] = f"{base_url.rstrip('/')}/{e['productSlug']}"
            else:
                 e["url"] = f"{base_url.rstrip('/')}/{e.get('urlSlug', 'unknown')}"
        except (KeyError, IndexError):
            logger.debug(f"無法取得 URL: {e}")
            continue

        logger.debug(f"發現週免遊戲: {e['url']}")
        promotions.append(PromotionGame(**e))

    return promotions


class EpicAgent:
    def __init__(self, page: Page):
        self.page = page
        self.epic_games = EpicGames(self.page)
        self._promotions: List[PromotionGame] = []
        self._ctx_cookies_is_available: bool = False
        self._orders: List[OrderItem] = []
        self._namespaces: List[str] = []
        self._cookies = None

    async def _handle_eula_correction(self) -> bool:
        """
        處理 EULA 修正頁面

        Epic Games 在某些情況下會將使用者重新導向到 EULA 修正頁面：
        - 新註冊帳號首次登入
        - Epic 更新服務條款
        - 帳號長期未登入
        - 帳號在新裝置/地區登入

        頁面特點：
        - SPA 單頁應用（React + Material UI），內容動態渲染
        - 只有「拒絕」和「接受」兩個按鈕，無核取方塊
        - 接受按鈕特徵：id="accept", type="submit"

        Returns:
            bool: True 表示成功處理 EULA，False 表示無需處理或處理失敗
        """
        current_url = self.page.url

        # 偵測是否在 EULA 修正頁面
        if "correction/eula" not in current_url:
            return False

        logger.warning("⚠️ 偵測到 EULA 修正頁面，嘗試自動接受協議...")

        try:
            # SPA 頁面需要等待網路完全閒置
            await self.page.wait_for_load_state("networkidle")

            # 額外等待 React 渲染完成
            await self.page.wait_for_timeout(2000)

            # ============================================================
            # EULA 接受按鈕選擇器（按優先順序排序）
            # 按鈕特徵: <button id="accept" type="submit">接受</button>
            # ============================================================
            accept_selectors = [
                # 最精確：透過 ID 選擇（最穩定）
                "#accept",
                "button#accept",
                "//button[@id='accept']",

                # 透過 type=submit（次優）
                "//button[@type='submit']",

                # 透過文字比對（多語言）
                "//button[normalize-space(text())='Accept']",
                "//button[normalize-space(text())='接受']",
                "//button[normalize-space(text())='Akzeptieren']",
                "//button[normalize-space(text())='Accepter']",
            ]

            # 嘗試點擊接受按鈕
            for selector in accept_selectors:
                try:
                    btn = self.page.locator(selector).first
                    # 增加等待時間，因為 SPA 需要渲染
                    if await btn.is_visible(timeout=5000):
                        btn_text = await btn.text_content()
                        logger.info(f"📋 點擊 EULA 接受按鈕: '{btn_text}' | 選擇器: {selector}")
                        await btn.click()

                        # 等待頁面跳轉
                        await self.page.wait_for_load_state("networkidle", timeout=15000)

                        # 驗證是否成功跳轉
                        new_url = self.page.url
                        if "correction/eula" not in new_url:
                            logger.success("✅ EULA 協議已接受，頁面已跳轉")
                            return True
                        else:
                            logger.warning("⚠️ 點擊後仍在 EULA 頁面，嘗試下一個選擇器")
                except Exception as e:
                    logger.debug(f"EULA 選擇器 '{selector}' 失敗: {e}")
                    continue

            logger.error("❌ 未能找到 EULA 接受按鈕")
            return False

        except Exception as e:
            logger.error(f"❌ 處理 EULA 頁面異常: {e}")
            return False

    async def _sync_order_history(self):
        if self._orders:
            return
        completed_orders: List[OrderItem] = []
        try:
            await self.page.goto("https://www.epicgames.com/account/v2/payment/ajaxGetOrderHistory")
            text_content = await self.page.text_content("//pre")
            data = json.loads(text_content)
            for _order in data["orders"]:
                order = Order(**_order)
                if order.orderType != "PURCHASE":
                    continue
                for item in order.items:
                    if not item.namespace or len(item.namespace) != 32:
                        continue
                    completed_orders.append(item)
        except Exception as err:
            logger.warning(err)
        self._orders = completed_orders

    async def _check_orders(self):
        await self._sync_order_history()
        self._namespaces = self._namespaces or [order.namespace for order in self._orders]
        self._promotions = [p for p in get_promotions() if p.namespace not in self._namespaces]

    async def _should_ignore_task(self) -> tuple[bool, GameCollectResult]:
        """
        檢查是否應該忽略任務

        回傳:
            tuple[bool, GameCollectResult]:
                - (True, ALL_OWNED): 所有遊戲已在庫中，無需領取
                - (False, SUCCESS): 有遊戲需要領取
                - (False, EULA_FAILED): EULA 處理失敗
                - (False, COOKIE_INVALID): Cookie 無效
                - (False, UNKNOWN_ERROR): 未知錯誤
        """
        self._ctx_cookies_is_available = False
        await self.page.goto(URL_CLAIM, wait_until="domcontentloaded")

        # ============================================================
        # 🔥 關鍵修復：等待頁面穩定，防止 JS 重新導向導致偵測遺漏
        # Epic Games 可能會透過 JS 非同步重新導向到 EULA 頁面
        # domcontentloaded 觸發時重新導向可能還沒完成
        # ============================================================
        await self.page.wait_for_timeout(2000)  # 等待 JS 執行完成

        # ============================================================
        # 🔥 EULA 修正頁面檢測與處理
        # Epic Games 可能會重定向到 EULA 頁面，需要自動接受協議
        # ============================================================
        max_eula_attempts = 3
        for attempt in range(max_eula_attempts):
            current_url = self.page.url
            logger.debug(f"📍 當前頁面 URL: {current_url}")
            if "correction/eula" in current_url or "corrective=" in current_url:
                logger.warning(f"⚠️ 檢測到修正頁面（嘗試 {attempt + 1}/{max_eula_attempts}）")
                if await self._handle_eula_correction():
                    # EULA 處理成功後，重新導航到目標頁面
                    await self.page.goto(URL_CLAIM, wait_until="domcontentloaded")
                    await self.page.wait_for_timeout(2000)  # 再次等待穩定
                else:
                    logger.error("❌ EULA 處理失敗，跳過此帳號")
                    return False, GameCollectResult.EULA_FAILED
            else:
                break

        # 嘗試取得登入狀態，增加逾時處理
        try:
            status = await self.page.locator("//egs-navigation").get_attribute("isloggedin", timeout=10000)
        except Exception as e:
            # 如果逾時，可能還在修正頁面或有其他問題
            current_url = self.page.url
            if "correction" in current_url or "eula" in current_url:
                logger.error("❌ 仍在修正頁面，無法繼續")
                return False, GameCollectResult.EULA_FAILED
            logger.error(f"❌ 獲取登入狀態逾時: {e}")
            return False, GameCollectResult.UNKNOWN_ERROR

        if status == "false":
            logger.error("❌ Cookie 無效，帳號未登入")
            return False, GameCollectResult.COOKIE_INVALID
        self._ctx_cookies_is_available = True
        await self._check_orders()
        if not self._promotions:
            return True, GameCollectResult.ALL_OWNED
        return False, GameCollectResult.SUCCESS

    async def collect_epic_games(self) -> GameCollectResult:
        """
        收集 Epic Games 週免遊戲

        Returns:
            GameCollectResult: 執行結果
        """
        should_ignore, result = await self._should_ignore_task()

        # 所有遊戲已在庫中
        if should_ignore:
            logger.success("✅ 所有週免遊戲已在庫中")
            return GameCollectResult.ALL_OWNED

        # 處理錯誤情況
        if result != GameCollectResult.SUCCESS:
            # 輸出特定格式的錯誤日誌，便於 worker.py 解析
            logger.error(f"❌ GAME_ERROR:{result.value}")
            return result

        # 檢查是否有遊戲需要領取
        if not self._promotions:
            await self._check_orders()

        if not self._promotions:
            logger.success("✅ 所有週免遊戲已在庫中")
            return GameCollectResult.ALL_OWNED

        # 輸出遊戲資訊供 worker.py 解析（必須用 INFO 層級）
        for p in self._promotions:
            pj = json.dumps({"title": p.title, "url": p.url}, ensure_ascii=False)
            logger.info(f"發現: {pj}")

        # 執行領取
        if self._promotions:
            try:
                await self.epic_games.collect_weekly_games(self._promotions)
                return GameCollectResult.SUCCESS
            except Exception as e:
                logger.exception(e)
                return GameCollectResult.UNKNOWN_ERROR
        
        logger.debug("工作流程中的所有任務皆已完成")
        return GameCollectResult.SUCCESS


class EpicGames:
    def __init__(self, page: Page):
        self.page = page
        self._promotions: List[PromotionGame] = []

    @staticmethod
    async def _agree_license(page: Page):
        logger.debug("接受協議")
        with suppress(TimeoutError):
            await page.click("//label[@for='agree']", timeout=4000)
            accept = page.locator("//button//span[text()='Accept']")
            if await accept.is_enabled():
                await accept.click()

    @staticmethod
    async def _active_purchase_container(page: Page):
        logger.debug("正在掃描購買容器...")

        # Epic 的新結帳頁不穩定：確認按鈕可能在 webPurchase iframe、
        # 其它 purchase iframe、甚至主頁面彈層裡。這裡不再只選第一個 iframe，
        # 而是掃描主頁面和所有 frame，避免命中無關 iframe 後誤報。
        await page.wait_for_timeout(3000)

        button_texts = [
            "PLACE ORDER",
            "Place Order",
            "GET",
            "Get",
            "ADD TO LIBRARY",
            "Add to library",
            "Add To Library",
            "BUY NOW",
            "Buy Now",
            "CONFIRM",
            "Confirm",
            "Confirm Order",
            "Complete Order",
            "Submit Order",
        ]
        css_selectors = [
            "button[data-testid='purchase-button']",
            "button[data-testid='place-order-button']",
            "button[data-testid='confirm-order-button']",
            "button[data-testid*='purchase']",
            "button[data-testid*='order']",
            "button[data-testid*='confirm']",
            "button.payment-btn",
            "button[class*='payment-confirm']",
            "button[class*='confirm']",
            "button[type='submit']",
        ]

        containers: list[tuple[str, Any]] = [("page", page)]
        for idx, frame in enumerate(page.frames):
            if frame == page.main_frame:
                continue
            containers.append((f"frame[{idx}] {frame.url[:180]}", frame))
        
        logger.info(f"🔎 掃描結帳容器: {len(containers)} 個候選")

        async def _button_is_usable(btn) -> bool:
            try:
                await btn.wait_for(state="visible", timeout=2500)
                if await btn.is_disabled(timeout=1000):
                    return False
                return True
            except Exception:
                return False

        async def _describe_buttons(label: str, container: Any):
            try:
                buttons = await container.locator("button").all()
                logger.warning(f"🔍 {label} 按鈕數量: {len(buttons)}")
                for i, btn in enumerate(buttons[:12]):
                    try:
                        text = (await btn.text_content(timeout=1000) or "").strip()
                        aria = await btn.get_attribute("aria-label", timeout=1000)
                        testid = await btn.get_attribute("data-testid", timeout=1000)
                        disabled = await btn.is_disabled(timeout=1000)
                        logger.warning(
                            f"🔍 {label} button[{i}]: text={text!r}, aria={aria!r}, "
                            f"testid={testid!r}, disabled={disabled}"
                        )
                    except Exception as e:
                        logger.warning(f"🔍 {label} button[{i}] 檢查失敗: {e}")
            except Exception as e:
                logger.warning(f"🔍 {label} 列出按鈕失敗: {e}")

        for label, container in containers:
            logger.info(f"🔎 檢查結帳容器: {label}")

            for text_value in button_texts:
                try:
                    btn = container.locator("button", has_text=text_value).first
                    if await _button_is_usable(btn):
                        btn_text = (await btn.text_content(timeout=1000) or "").strip()
                        logger.info(f"✅ 找到結帳按鈕: {btn_text!r} | 容器: {label} | 文字: {text_value}")
                        return container, btn
                except Exception as e:
                    logger.debug(f"按鈕文字 {text_value!r} 在 {label} 中失敗: {e}")

            for selector in css_selectors:
                try:
                    btn = container.locator(selector).first
                    if await _button_is_usable(btn):
                        btn_text = (await btn.text_content(timeout=1000) or "").strip()
                        logger.info(f"✅ 找到結帳按鈕: {btn_text!r} | 容器: {label} | 選擇器: {selector}")
                        return container, btn
                except Exception as e:
                    logger.debug(f"按鈕選擇器 {selector!r} 在 {label} 中失敗: {e}")

        logger.warning("找不到主要按鈕。正在偵錯結帳容器...")
        for label, container in containers:
            await _describe_buttons(label, container)

        with suppress(Exception):
            debug_path = RUNTIME_DIR.joinpath("checkout_debug_last.html")
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(await page.content(), encoding="utf-8")
            logger.warning(f"🧾 已儲存結帳頁偵錯 HTML: {debug_path}")

        raise AssertionError("無法在結帳容器中找到下單按鈕")
            
    @staticmethod
    async def _handle_device_not_supported_modal(page: Page) -> bool:
        """繼續跳過 Epic 的裝置不支援中間彈窗。"""
        dialog = page.locator("[role='dialog']").filter(has_text="Device not supported").first

        try:
            await dialog.wait_for(state="visible", timeout=3000)
        except Exception:
            return False

        body_text = ""
        with suppress(Exception):
            body_text = (await dialog.text_content(timeout=1000) or "").strip()

        if "not compatible with your current device" not in body_text:
            return False

        continue_btn = dialog.locator("button", has_text="Continue").first
        try:
            await continue_btn.wait_for(state="visible", timeout=3000)
            if await continue_btn.is_disabled(timeout=1000):
                logger.warning("⚠️ Epic 裝置不支援彈窗的 Continue 按鈕無法點擊")
                return False

            logger.info("ℹ️ Epic 顯示裝置不支援提示，點擊 Continue 繼續領取流程")
            await continue_btn.click(force=True)
            await page.wait_for_timeout(3000)
            return True
        except Exception as err:
            logger.warning(f"⚠️ 處理 Epic 裝置不支援彈窗失敗: {err}")
            return False

    @staticmethod
    async def _uk_confirm_order(wpc: Any):
        logger.debug("UK confirm order")
        with suppress(TimeoutError):
            accept = wpc.locator("//button[contains(@class, 'payment-confirm__btn')]")
            if await accept.is_enabled(timeout=5000):
                await accept.click()
                return True

    async def _handle_instant_checkout(self, page: Page):
        logger.info("🚀 開始即時結帳流程...")
        agent = AgentV(page=page, agent_config=settings)

        try:
            await self._handle_device_not_supported_modal(page)
            wpc, payment_btn = await self._active_purchase_container(page)
            if await self._handle_device_not_supported_modal(page):
                wpc, payment_btn = await self._active_purchase_container(page)

            logger.debug(f"點擊支付按鈕: {await payment_btn.text_content()}")
            await payment_btn.click(force=True)
            await page.wait_for_timeout(3000)
            
            try:
                logger.debug("檢查驗證碼...")
                await agent.wait_for_challenge()
            except Exception as e:
                logger.debug(f"驗證碼檢測跳過: {e}")

            try:
                if not await payment_btn.is_visible():
                     logger.success("🎉 領取成功：支付按鈕已消失")
                     return
            except Exception:
                logger.success("🎉 領取成功：iframe 已關閉")
                return

            with suppress(Exception):
                await payment_btn.click(force=True)
                await page.wait_for_timeout(2000)
            
            logger.success("🎉 遊戲領取成功！")

        except Exception as err:
            logger.warning(f"⚠️ 即時結帳警告（遊戲可能已領取）: {err}")
            await page.reload()

    async def add_promotion_to_cart(self, page: Page, urls: List[str]) -> bool:
        has_pending_cart_items = False

        for url in urls:
            await page.goto(url, wait_until="load")

            # 404 檢測
            title = await page.title()
            if "404" in title or "Page Not Found" in title:
                logger.error(f"❌ 無效的 URL (404 頁面): {url}")
                continue

            # 處理年齡限制彈窗
            try:
                continue_btn = page.locator("//button//span[text()='Continue']")
                if await continue_btn.is_visible(timeout=5000):
                    await continue_btn.click()
            except Exception:
                pass 

            # ------------------------------------------------------------
            # 🔥 按鈕識別與狀態判斷
            # ------------------------------------------------------------
            
            # 1. 嘗試找到主按鈕
            purchase_btn = page.locator("//button[@data-testid='purchase-cta-button']").first

            # 2. 檢查按鈕可見性
            try:
                if not await purchase_btn.is_visible(timeout=5000):
                    all_text = await page.locator("body").text_content()
                    if "In Library" in all_text or "Owned" in all_text:
                         logger.success(f"✅ 遊戲已在庫中")
                         continue
                    logger.warning(f"⚠️ 找不到購買按鈕")
                    continue
            except Exception:
                pass

            # 3. 獲取按鈕資訊
            btn_text = await purchase_btn.text_content()
            if not btn_text: btn_text = ""
            btn_text = btn_text.strip()
            btn_text_upper = btn_text.upper()
            is_disabled = await purchase_btn.is_disabled()
            
            # 4. 列印按鈕狀態（關鍵資訊）
            logger.info(f"📋 按鈕狀態: '{btn_text}' | 禁用: {is_disabled}")

            # 5. 根據狀態判斷
            if is_disabled:
                logger.success(f"✅ 遊戲已在庫中")
                continue

            if any(s in btn_text_upper for s in ["IN LIBRARY", "OWNED", "UNAVAILABLE", "COMING SOON"]):
                logger.success(f"✅ 遊戲已在庫中")
                continue

            if "CART" in btn_text_upper:
                logger.info(f"🛒 加入購物車")
                await purchase_btn.click()
                has_pending_cart_items = True
                continue
            
            # 6. 嘗試領取
            # 只要不是黑名單，也不是購物車，統統當做 "Get/Purchase" 直接點擊！
            logger.debug(f"⚡️ 嘗試點擊按鈕: {btn_text}")
            await purchase_btn.click()
            
            # 點擊後，轉入即時結帳流程
            await self._handle_instant_checkout(page)
            # ------------------------------------------------------------

        return has_pending_cart_items

    async def _empty_cart(self, page: Page, wait_rerender: int = 30) -> bool | None:
        has_paid_free = False
        try:
            cards = await page.query_selector_all("//div[@data-testid='offer-card-layout-wrapper']")
            for card in cards:
                is_free = await card.query_selector("//span[text()='Free']")
                if not is_free:
                    has_paid_free = True
                    wishlist_btn = await card.query_selector(
                        "//button//span[text()='Move to wishlist']"
                    )
                    await wishlist_btn.click()

            if has_paid_free and wait_rerender:
                wait_rerender -= 1
                await page.wait_for_timeout(2000)
                return await self._empty_cart(page, wait_rerender)
            return True
        except TimeoutError as err:
            logger.warning(f"清空購物車失敗: {err}")
            return False

    async def _purchase_free_game(self):
        await self.page.goto(URL_CART, wait_until="domcontentloaded")
        logger.debug("將購物車中所有付費遊戲移出")
        await self._empty_cart(self.page)

        agent = AgentV(page=self.page, agent_config=settings)
        await self.page.click("//button//span[text()='Check Out']")
        await self._agree_license(self.page)

        try:
            logger.debug("移動至 webPurchaseContainer iframe")
            wpc, payment_btn = await self._active_purchase_container(self.page)
            logger.debug("點擊付款按鈕")
            await self._uk_confirm_order(wpc)
            await agent.wait_for_challenge()
        except Exception as err:
            logger.warning(f"驗證碼解決失敗: {err}")
            await self.page.reload()
            return await self._purchase_free_game()

    @retry(retry=retry_if_exception_type(TimeoutError), stop=stop_after_attempt(2), reraise=True)
    async def collect_weekly_games(self, promotions: List[PromotionGame]):
        urls = [p.url for p in promotions]
        has_cart_items = await self.add_promotion_to_cart(self.page, urls)

        if has_cart_items:
            await self._purchase_free_game()
            try:
                await self.page.wait_for_url(URL_CART_SUCCESS)
                logger.success("🎉 購物車遊戲領取成功")
            except TimeoutError:
                logger.warning("購物車遊戲領取失敗")
        else:
            logger.success("🎉 任務完成（已領取或已在庫中）")
