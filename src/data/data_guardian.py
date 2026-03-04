"""
数据护城河 — 快照备份 + 恢复 + 防误删保护

踩坑背景: cleanup_stale_data() 测试用假池清理了真实 103 只股票的数据。
本模块提供:
- snapshot(): 对关键数据创建轻量快照 (~15MB 压缩后 ~3MB)
- restore(): 从快照恢复
- list_snapshots(): 列出所有可用快照
"""
import logging
import tarfile
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import sys
sys.path.insert(0, str(__file__).rsplit("/src", 1)[0])
from config.settings import DATA_DIR, PRICE_DIR, FUNDAMENTAL_DIR, POOL_DIR, MARKET_DB_PATH

logger = logging.getLogger(__name__)

BACKUP_DIR = DATA_DIR / ".backups"
MAX_SNAPSHOTS = 10
COMPANY_DB = DATA_DIR / "company.db"
MARKET_DB = MARKET_DB_PATH


def _get_backup_targets() -> Dict[str, Path]:
    """运行时获取备份目标 (支持 monkeypatch 测试)。"""
    import src.data.data_guardian as _self
    return {
        "price": _self.PRICE_DIR,
        "fundamental": _self.FUNDAMENTAL_DIR,
        "pool": _self.POOL_DIR,
    }


def snapshot(reason: str = "manual") -> Optional[Path]:
    """
    对关键数据创建轻量快照。

    备份内容:
    - data/market.db (主时序数据库)
    - data/price/*.csv (副写备份)
    - data/fundamental/*.json
    - data/company.db
    - data/pool/universe.json

    存储: data/.backups/YYYYMMDD_HHMMSS_{reason}.tar.gz
    保留策略: 最多 MAX_SNAPSHOTS 个快照，超出删除最旧的

    Returns:
        快照文件路径，失败返回 None
    """
    import src.data.data_guardian as _self
    backup_dir = _self.BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(c if c.isalnum() or c in "-_" else "_" for c in reason)
    filename = f"{timestamp}_{safe_reason}.tar.gz"
    snapshot_path = backup_dir / filename

    try:
        with tarfile.open(snapshot_path, "w:gz") as tar:
            files_added = 0

            for name, dir_path in _get_backup_targets().items():
                if dir_path.exists():
                    for f in dir_path.iterdir():
                        if f.is_file():
                            arcname = f"{name}/{f.name}"
                            tar.add(str(f), arcname=arcname)
                            files_added += 1

            company_db = _self.COMPANY_DB
            if company_db.exists():
                tar.add(str(company_db), arcname="company.db")
                files_added += 1

            market_db = _self.MARKET_DB
            if market_db.exists():
                tar.add(str(market_db), arcname="market.db")
                files_added += 1

        logger.info(f"快照创建成功: {filename} ({files_added} 个文件)")

        _enforce_retention()

        return snapshot_path

    except Exception as e:
        logger.error(f"快照创建失败: {e}")
        if snapshot_path.exists():
            snapshot_path.unlink()
        return None


def restore(snapshot_path: str) -> Dict[str, int]:
    """
    从快照恢复数据。

    Args:
        snapshot_path: 快照文件路径 (字符串或 Path)

    Returns:
        恢复统计 {"files_restored": N, "errors": N}
    """
    path = Path(snapshot_path)
    if not path.exists():
        raise FileNotFoundError(f"快照文件不存在: {snapshot_path}")

    stats = {"files_restored": 0, "errors": 0}

    try:
        with tarfile.open(path, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue

                try:
                    target = _resolve_restore_target(member.name)
                    if target is None:
                        logger.warning(f"跳过未知路径: {member.name}")
                        continue

                    target.parent.mkdir(parents=True, exist_ok=True)

                    f = tar.extractfile(member)
                    if f:
                        with open(target, "wb") as out:
                            out.write(f.read())
                        stats["files_restored"] += 1

                except Exception as e:
                    logger.error(f"恢复文件失败 {member.name}: {e}")
                    stats["errors"] += 1

    except Exception as e:
        logger.error(f"打开快照文件失败: {e}")
        raise

    logger.info(f"恢复完成: {stats['files_restored']} 个文件, {stats['errors']} 个错误")
    return stats


def list_snapshots() -> List[Dict]:
    """
    列出所有可用快照。

    Returns:
        [{
            "path": Path,
            "filename": str,
            "created_at": str,
            "reason": str,
            "size_mb": float,
        }, ...]
    """
    import src.data.data_guardian as _self
    backup_dir = _self.BACKUP_DIR

    if not backup_dir.exists():
        return []

    snapshots = []
    for f in sorted(backup_dir.glob("*.tar.gz")):
        parts = f.stem.replace(".tar", "").split("_", 2)
        if len(parts) >= 3:
            created_at = f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]} {parts[1][:2]}:{parts[1][2:4]}:{parts[1][4:6]}"
            reason = parts[2]
        else:
            created_at = "unknown"
            reason = "unknown"

        snapshots.append({
            "path": f,
            "filename": f.name,
            "created_at": created_at,
            "reason": reason,
            "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
        })

    return snapshots


def _resolve_restore_target(arcname: str) -> Optional[Path]:
    """将归档内路径映射回实际文件路径。"""
    import src.data.data_guardian as _self

    if arcname == "company.db":
        return _self.COMPANY_DB
    if arcname == "market.db":
        return _self.MARKET_DB

    parts = arcname.split("/", 1)
    if len(parts) != 2:
        return None

    category, filename = parts
    dir_map = {
        "price": _self.PRICE_DIR,
        "fundamental": _self.FUNDAMENTAL_DIR,
        "pool": _self.POOL_DIR,
    }

    target_dir = dir_map.get(category)
    if target_dir is None:
        return None

    return target_dir / filename


def _enforce_retention():
    """保留策略: 最多 MAX_SNAPSHOTS 个快照，超出删除最旧的。"""
    import src.data.data_guardian as _self
    backup_dir = _self.BACKUP_DIR
    max_snapshots = _self.MAX_SNAPSHOTS

    snapshots = sorted(backup_dir.glob("*.tar.gz"))
    while len(snapshots) > max_snapshots:
        oldest = snapshots.pop(0)
        oldest.unlink()
        logger.info(f"删除旧快照: {oldest.name}")
