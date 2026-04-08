"""Tests for PDF conversion helper."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from terminal.pdf_report import html_to_pdf


class TestHtmlToPdf:
    def test_converts_html_to_pdf_via_weasyprint(self, tmp_path):
        html = tmp_path / "report.html"
        html.write_text("<html><body>Test</body></html>", encoding="utf-8")

        fake_html = MagicMock()
        fake_html.write_pdf = MagicMock(
            side_effect=lambda path: Path(path).write_bytes(b"%PDF-1.4")
        )

        with patch("terminal.pdf_report.WeasyHTML", return_value=fake_html) as mock_cls:
            result = html_to_pdf(html)

        assert result == html.with_suffix(".pdf")
        assert result.exists()
        mock_cls.assert_called_once_with(filename=str(html))
        fake_html.write_pdf.assert_called_once_with(str(result))

    def test_returns_none_for_missing_file(self):
        assert html_to_pdf("/nonexistent/report.html") is None

    def test_returns_none_when_weasyprint_missing(self, tmp_path):
        html = tmp_path / "report.html"
        html.write_text("<html></html>", encoding="utf-8")

        with patch("terminal.pdf_report.WeasyHTML", None):
            assert html_to_pdf(html) is None

    def test_returns_none_on_conversion_error(self, tmp_path):
        html = tmp_path / "report.html"
        html.write_text("<html></html>", encoding="utf-8")

        fake_html = MagicMock()
        fake_html.write_pdf.side_effect = RuntimeError("boom")

        with patch("terminal.pdf_report.WeasyHTML", return_value=fake_html):
            assert html_to_pdf(html) is None
