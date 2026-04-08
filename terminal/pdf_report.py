"""HTML to PDF conversion helpers."""

from __future__ import annotations

import logging
from pathlib import Path

try:
    from weasyprint import HTML as WeasyHTML
except ImportError:  # pragma: no cover - exercised via unit tests
    WeasyHTML = None

logger = logging.getLogger(__name__)


def html_to_pdf(html_path: Path | str) -> Path | None:
    """Convert an HTML report into a PDF saved alongside the source file."""
    html_path = Path(html_path)
    if not html_path.exists():
        logger.warning("[PDF] HTML 文件不存在: %s", html_path)
        return None

    if WeasyHTML is None:
        logger.warning("[PDF] weasyprint 未安装，跳过 PDF 转换")
        return None

    pdf_path = html_path.with_suffix(".pdf")
    try:
        WeasyHTML(filename=str(html_path)).write_pdf(str(pdf_path))
        logger.info("[PDF] 转换完成: %s", pdf_path)
        return pdf_path
    except Exception as exc:
        logger.error("[PDF] 转换失败: %s", exc)
        return None
