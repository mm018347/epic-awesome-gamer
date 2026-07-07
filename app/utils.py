# -*- coding: utf-8 -*-
"""
紀錄檔設定模組

控制臺紀錄檔策略：
- 只顯示關鍵資訊（啟動、登入、驗證碼、遊戲領取、錯誤）
- 過濾宂長的詳細紀錄檔
- 中文顯示

檔案紀錄檔策略：
- 按日期分類儲存，方便尋找和清理
- 檔名格式：runtime-2026-03-22.log / error-2026-03-22.log
- 保留 7 天
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from loguru import logger

def timezone_filter(record):
    """時區轉檔過濾器"""
    record["time"] = record["time"].astimezone(ZoneInfo("Asia/Taipei"))
    return True

# 控制臺只顯示的關鍵紀錄檔關鍵詞
CONSOLE_KEYWORDS = [
    # 啟動設定
    "API 提供商",
    "驗證碼模型",
    "主力模型",
    "補丁載入成功",
    # 登入狀態
    "已登入",
    "登入成功",
    # 驗證碼結果
    "驗證碼透過",
    "驗證碼逾時",
    # 遊戲領取
    "已在庫中",
    "領取成功",
    "任務完成",
    "按鈕狀態",
    "發現:",
    # 錯誤
    "錯誤",
    "失敗",
    "警告",
]

# 控制臺要過濾掉的詳細紀錄檔關鍵詞（即使級別比對也不顯示）
SUPPRESS_KEYWORDS = [
    "原始回應",
    "JSON 解析",
    "呼叫 OpenAI 相容 API",
    "檔案已快取",
    "response_schema",
    "備用模型",
    "hsw script",
    "is read-only",
    "btoa",
]

def console_filter(record):
    """
    控制臺過濾器：只顯示關鍵紀錄檔

    規則：
    1. ERROR 及以上級別：始終顯示
    2. SUCCESS 級別：顯示關鍵操作結果
    3. WARNING 級別：顯示重要警告
    4. INFO 級別：只顯示包含關鍵詞的紀錄檔
    5. DEBUG 級別：不顯示在控制臺
    """
    level = record["level"].name
    message = record["message"]

    # DEBUG 級別不顯示在控制臺
    if level == "DEBUG":
        return False

    # ERROR 及以上始終顯示
    if level in ("ERROR", "CRITICAL"):
        return True

    # 檢查是否在抑製列表中
    for keyword in SUPPRESS_KEYWORDS:
        if keyword in message:
            return False

    # SUCCESS 級別顯示關鍵操作
    if level == "SUCCESS":
        return True

    # WARNING 級別過濾掉次要警告
    if level == "WARNING":
        # 過濾掉重試警告（太多）
        if "try to retry" in message or "retry the strategy" in message:
            return False
        return True

    # INFO 級別：只顯示包含關鍵詞的紀錄檔
    for keyword in CONSOLE_KEYWORDS:
        if keyword in message:
            return True

    return False

def init_log(**sink_channel):
    """
    初始化紀錄檔系統

    控制臺：精簡輸出，只顯示關鍵資訊
    檔案：按日期分類儲存，保留 7 天
    """
    logger.remove()

    # 控制臺：使用過濾器，只顯示關鍵紀錄檔
    logger.add(
        sink=sys.stdout,
        level="INFO",
        filter=console_filter,
        format="<green>{time:MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    )

    # 錯誤紀錄檔檔案：按日期儲存，格式 error-2026-03-22.log
    if sink_channel.get("error"):
        error_path = Path(sink_channel.get("error"))
        log_dir = error_path.parent
        log_dir.mkdir(parents=True, exist_ok=True)

        # 使用日期作為檔名字尾
        date_str = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
        error_log_file = log_dir / f"error-{date_str}.log"

        logger.add(
            sink=str(error_log_file),
            level="ERROR",
            rotation="00:00",  # 每天午夜輪轉
            filter=timezone_filter,
            retention="7 days",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
            encoding="utf-8",
        )

    # 執行時紀錄檔檔案：按日期儲存，格式 runtime-2026-03-22.log
    if sink_channel.get("runtime"):
        runtime_path = Path(sink_channel.get("runtime"))
        log_dir = runtime_path.parent
        log_dir.mkdir(parents=True, exist_ok=True)

        # 使用日期作為檔名字尾
        date_str = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
        runtime_log_file = log_dir / f"runtime-{date_str}.log"

        logger.add(
            sink=str(runtime_log_file),
            level="DEBUG",
            rotation="00:00",  # 每天午夜輪轉
            filter=timezone_filter,
            retention="7 days",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
            encoding="utf-8",
        )

    return logger
