"""Tests for shared Telegram helpers."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.telegram_bot import (
    _resolve_chat_id,
    send_document,
    send_message,
    send_photo,
    split_message,
)


class TestResolveChatId:
    @patch("src.telegram_bot.TELEGRAM_CHAT_ID", "111")
    @patch("src.telegram_bot.TELEGRAM_GROUP_CHAT_ID", "222")
    def test_private_returns_chat_id(self):
        assert _resolve_chat_id("private") == "111"

    @patch("src.telegram_bot.TELEGRAM_CHAT_ID", "111")
    @patch("src.telegram_bot.TELEGRAM_GROUP_CHAT_ID", "222")
    def test_group_returns_group_chat_id(self):
        assert _resolve_chat_id("group") == "222"


class TestSplitMessage:
    def test_keeps_short_message_intact(self):
        assert split_message("hello") == ["hello"]

    def test_splits_on_marker_when_both_halves_fit(self):
        text = "A" * 1900 + "*D. Dollar Volume*" + "B" * 1900
        parts = split_message(text, split_marker="*D. Dollar Volume*", limit=3000)
        assert len(parts) == 2
        assert parts[1].startswith("*D. Dollar Volume*")

    def test_falls_back_to_fixed_chunks_without_dropping_tail(self):
        text = "X" * 9500
        parts = split_message(text, limit=4000)
        assert len(parts) == 3
        assert "".join(parts) == text


class TestSendMessage:
    @patch("src.telegram_bot.TELEGRAM_BOT_TOKEN", "tok")
    @patch("src.telegram_bot.TELEGRAM_CHAT_ID", "111")
    @patch("src.telegram_bot.requests.post")
    def test_send_private(self, mock_post):
        mock_post.return_value = MagicMock()
        mock_post.return_value.raise_for_status = MagicMock()

        assert send_message("hello", channel="private") is True

        payload = mock_post.call_args.kwargs["json"]
        assert payload["chat_id"] == "111"

    @patch("src.telegram_bot.TELEGRAM_BOT_TOKEN", "tok")
    @patch("src.telegram_bot.TELEGRAM_GROUP_CHAT_ID", "222")
    @patch("src.telegram_bot.requests.post")
    def test_send_group(self, mock_post):
        mock_post.return_value = MagicMock()
        mock_post.return_value.raise_for_status = MagicMock()

        assert send_message("hello", channel="group") is True

        payload = mock_post.call_args.kwargs["json"]
        assert payload["chat_id"] == "222"

    @patch("src.telegram_bot.TELEGRAM_BOT_TOKEN", "")
    def test_skip_when_missing_token(self):
        assert send_message("hello") is False

    @patch("src.telegram_bot.TELEGRAM_BOT_TOKEN", "tok")
    @patch("src.telegram_bot.TELEGRAM_CHAT_ID", "111")
    @patch("src.telegram_bot.requests.post", side_effect=Exception("net"))
    def test_retry_on_failure(self, mock_post):
        assert send_message("hello", max_retries=2) is False
        assert mock_post.call_count == 2


class TestSendDocument:
    @patch("src.telegram_bot.TELEGRAM_BOT_TOKEN", "tok")
    @patch("src.telegram_bot.TELEGRAM_GROUP_CHAT_ID", "222")
    @patch("src.telegram_bot.requests.post")
    def test_send_pdf(self, mock_post, tmp_path):
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"%PDF-fake")
        mock_post.return_value = MagicMock()
        mock_post.return_value.raise_for_status = MagicMock()

        assert send_document(str(pdf), caption="Test", channel="group") is True

        assert mock_post.call_args.kwargs["data"]["chat_id"] == "222"

    def test_missing_file_returns_false(self):
        assert send_document("/nonexistent.pdf") is False


class TestSendPhoto:
    @patch("src.telegram_bot.TELEGRAM_BOT_TOKEN", "tok")
    @patch("src.telegram_bot.TELEGRAM_GROUP_CHAT_ID", "222")
    @patch("src.telegram_bot.requests.post")
    def test_send_png(self, mock_post, tmp_path):
        png = tmp_path / "section.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        mock_post.return_value = MagicMock()
        mock_post.return_value.raise_for_status = MagicMock()

        assert send_photo(str(png), caption="Section", channel="group") is True

        assert mock_post.call_args.kwargs["data"]["chat_id"] == "222"
        assert "photo" in mock_post.call_args.kwargs["files"]

    def test_missing_file_returns_false(self):
        assert send_photo("/nonexistent.png") is False
