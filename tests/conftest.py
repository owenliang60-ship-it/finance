"""
全局测试守卫: 防止测试摧毁真实数据文件。

背景: cleanup_stale_data() 测试曾用假池删掉 103 只股票的真实 CSV。
此 fixture 在测试开始时记录 data/ 文件清单，结束后对比，
如果文件被删除则报告警告 (不 fail，因为某些测试可能在 tmp 中操作)。
"""
import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# 需要保护的子目录
PROTECTED_DIRS = ["price", "fundamental", "pool"]


def _snapshot_file_list() -> set:
    """记录 data/ 下受保护目录的文件清单。"""
    files = set()
    for subdir in PROTECTED_DIRS:
        dir_path = DATA_DIR / subdir
        if dir_path.exists():
            for f in dir_path.iterdir():
                if f.is_file():
                    files.add(str(f.relative_to(DATA_DIR)))
    # company.db
    db = DATA_DIR / "company.db"
    if db.exists():
        files.add("company.db")
    return files


@pytest.fixture(autouse=True, scope="session")
def guard_real_data():
    """
    全局守卫: 记录测试开始时 data/ 目录的文件清单，
    测试结束后对比，如果文件被删除则报告。
    """
    before = _snapshot_file_list()
    yield
    after = _snapshot_file_list()
    deleted = before - after
    if deleted:
        msg = f"测试期间 data/ 中有文件被删除: {deleted}"
        logger.error(msg)
        # 使用 warnings 报告，不直接 fail (避免误报 tmpdir 场景)
        import warnings
        warnings.warn(msg, UserWarning)


@pytest.fixture(autouse=True)
def _no_real_portfolio_sheet(monkeypatch):
    """Holdings overlays read the Google Sheet; tests must never download the
    real book. Tests that need an ID set a fake one via monkeypatch.setenv."""
    monkeypatch.delenv("PORTFOLIO_SHEET_ID", raising=False)
