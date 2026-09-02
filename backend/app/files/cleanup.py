"""上传文件 TTL 清理（B5）：启动时后台扫一遍，删除超过 TTL 的落盘附件。

- 存储是平面目录（uuid.ext，files/router.py 写入），只扫一层文件
- 异常一律吞掉记日志：清理失败绝不阻断启动
"""
import logging
import time
from pathlib import Path

from app.core.config import settings

_logger = logging.getLogger("app.files.cleanup")


async def sweep_expired_uploads() -> None:
    root = Path(settings.UPLOAD_DIR)
    cutoff = time.time() - settings.UPLOAD_TTL_DAYS * 86400
    removed = 0
    try:
        if not root.exists():
            return
        for entry in root.iterdir():
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue  # 单文件失败不中断整轮清理
    except Exception:
        _logger.warning("uploads TTL 清理失败（忽略）", exc_info=True)
        return
    if removed:
        _logger.info("uploads TTL 清理：删除 %d 个过期附件（TTL=%d 天）", removed, settings.UPLOAD_TTL_DAYS)
