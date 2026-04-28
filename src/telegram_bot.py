"""Shared Telegram delivery helpers for private and group routing."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_GROUP_CHAT_ID,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{}"
DEFAULT_MESSAGE_LIMIT = 4000


def _resolve_chat_id(channel: str) -> str:
    """Resolve a logical channel name to its Telegram chat_id."""
    if channel == "group":
        return TELEGRAM_GROUP_CHAT_ID
    return TELEGRAM_CHAT_ID


def split_message(
    text: str,
    split_marker: str | None = None,
    limit: int = DEFAULT_MESSAGE_LIMIT,
) -> list[str]:
    """Split a Telegram message without dropping trailing content."""
    if len(text) <= limit:
        return [text]

    if split_marker:
        split_idx = text.rfind(split_marker)
        if 0 < split_idx < len(text):
            first = text[:split_idx].strip()
            second = text[split_idx:].strip()
            if first and second and len(first) <= limit and len(second) <= limit:
                return [first, second]

    return [
        text[idx : idx + limit].strip()
        for idx in range(0, len(text), limit)
        if text[idx : idx + limit].strip()
    ]


def send_message(
    text: str,
    channel: str = "private",
    max_retries: int = 3,
) -> bool:
    """Send a text message to the selected Telegram channel."""
    token = TELEGRAM_BOT_TOKEN
    chat_id = _resolve_chat_id(channel)

    if not token or not chat_id:
        logger.info("[Telegram] 未配置 (%s)，跳过发送", channel)
        return False

    url = API_BASE.format(token) + "/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            logger.info("[Telegram] 消息已发送 → %s", channel)
            return True
        except Exception as exc:
            logger.warning("[Telegram] 第%d次发送失败 (%s): %s", attempt, channel, exc)
            if attempt < max_retries:
                time.sleep(attempt * 2)

    return False


def send_document(
    file_path: str,
    caption: str = "",
    channel: str = "group",
    max_retries: int = 3,
) -> bool:
    """Send a document such as a PDF to the selected Telegram channel."""
    path = Path(file_path)
    if not path.exists():
        logger.warning("[Telegram] 文件不存在: %s", file_path)
        return False

    token = TELEGRAM_BOT_TOKEN
    chat_id = _resolve_chat_id(channel)

    if not token or not chat_id:
        logger.info("[Telegram] 未配置 (%s)，跳过发送", channel)
        return False

    url = API_BASE.format(token) + "/sendDocument"

    for attempt in range(1, max_retries + 1):
        try:
            with path.open("rb") as handle:
                data = {"chat_id": chat_id, "parse_mode": "Markdown"}
                if caption:
                    data["caption"] = caption
                resp = requests.post(
                    url, data=data, files={"document": handle}, timeout=60
                )
                resp.raise_for_status()
            logger.info("[Telegram] 文件已发送 → %s: %s", channel, path.name)
            return True
        except Exception as exc:
            logger.warning(
                "[Telegram] 文件发送第%d次失败 (%s): %s", attempt, channel, exc
            )
            if attempt < max_retries:
                time.sleep(attempt * 2)

    return False


def send_photo(
    file_path: str,
    caption: str = "",
    channel: str = "group",
    max_retries: int = 3,
) -> bool:
    """Send an image as an inline Telegram photo."""
    path = Path(file_path)
    if not path.exists():
        logger.warning("[Telegram] 图片不存在: %s", file_path)
        return False

    token = TELEGRAM_BOT_TOKEN
    chat_id = _resolve_chat_id(channel)

    if not token or not chat_id:
        logger.info("[Telegram] 未配置 (%s)，跳过发送", channel)
        return False

    url = API_BASE.format(token) + "/sendPhoto"

    for attempt in range(1, max_retries + 1):
        try:
            with path.open("rb") as handle:
                data = {"chat_id": chat_id, "parse_mode": "Markdown"}
                if caption:
                    data["caption"] = caption
                resp = requests.post(
                    url, data=data, files={"photo": handle}, timeout=60
                )
                resp.raise_for_status()
            logger.info("[Telegram] 图片已发送 → %s: %s", channel, path.name)
            return True
        except Exception as exc:
            logger.warning(
                "[Telegram] 图片发送第%d次失败 (%s): %s", attempt, channel, exc
            )
            if attempt < max_retries:
                time.sleep(attempt * 2)

    return False
