"""Data retention sweep (Phase 1c data governance).

Enterprise compliance: uploads and their outputs are deleted after
``DATA_RETENTION_DAYS`` days (0 = disabled, the default — nothing changes for
existing deployments unless an operator opts in). Deletion goes through the
same ``delete_file`` used by the API so uploads, outputs, store entries and
job links are removed together, and every removal leaves an audit entry.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from app.core.audit import audit_log
from app.core.config import settings

logger = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 6 * 3600
_FIRST_SWEEP_DELAY_SECONDS = 120
_COLD_ARCHIVE_INTERVAL_SECONDS = 15 * 60
_COLD_ARCHIVE_FIRST_DELAY_SECONDS = 120


def _parse_created_at(raw: object) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


async def retention_sweep() -> int:
    """Delete files older than the retention window. Returns deleted count."""
    days = int(settings.DATA_RETENTION_DAYS or 0)
    if days <= 0:
        return 0
    from app.services.file_management_service import delete_file, file_store

    cutoff = datetime.now(UTC) - timedelta(days=days)
    expired: list[str] = []
    for file_id, info in file_store.items():
        if not isinstance(info, dict):
            continue
        created = _parse_created_at(info.get("created_at"))
        if created is not None and created < cutoff:
            expired.append(str(file_id))

    deleted = 0
    for file_id in expired:
        try:
            await delete_file(file_id)
            audit_log(
                "retention_delete",
                "file",
                file_id,
                user="system",
                detail={"retention_days": days},
            )
            deleted += 1
        except Exception as exc:  # keep sweeping; one stuck file must not stop the rest
            logger.warning("retention sweep: unable to delete %s: %s", file_id, exc)
    if deleted:
        logger.info("retention sweep: deleted %d file(s) older than %d days", deleted, days)
    return deleted


async def trash_sweep() -> int:
    """清空超期回收站（R1-4）：deleted_at 超过 TRASH_RETENTION_DAYS 的真删。

    与 DATA_RETENTION_DAYS 独立——回收站清扫始终开启（软删不清扫=磁盘只增不减）。
    """
    days = int(settings.TRASH_RETENTION_DAYS or 7)
    from app.services.file_management_service import delete_file, file_store

    cutoff = datetime.now(UTC) - timedelta(days=days)
    expired: list[str] = []
    for file_id, info in file_store.items():
        if not isinstance(info, dict) or not info.get("deleted_at"):
            continue
        deleted_at = _parse_created_at(info.get("deleted_at"))
        if deleted_at is not None and deleted_at < cutoff:
            expired.append(str(file_id))

    purged = 0
    for file_id in expired:
        try:
            await delete_file(file_id)
            audit_log(
                "trash_purge",
                "file",
                file_id,
                user="system",
                detail={"trash_retention_days": days},
            )
            purged += 1
        except Exception as exc:
            logger.warning("trash sweep: unable to purge %s: %s", file_id, exc)

    # 结构化数据集回收站同窗清扫（F1-1）
    try:
        from app.services.structured_store import get_structured_store

        ds_purged = get_structured_store().purge_expired_trashed_datasets(
            older_than_iso=cutoff.isoformat()
        )
        if ds_purged:
            audit_log(
                "trash_purge",
                "structured_dataset",
                f"{ds_purged} datasets",
                user="system",
                detail={"trash_retention_days": days},
            )
            purged += ds_purged
    except Exception:
        logger.exception("trash sweep: dataset purge failed")

    if purged:
        logger.info("trash sweep: purged %d item(s) past %d-day window", purged, days)
    return purged


async def cold_archive_sweep() -> int:
    """Move redacted originals into encrypted cold storage after the hot window."""
    minutes = int(getattr(settings, "ORIGINAL_RETENTION_MINUTES", 1440) or 1440)
    if minutes < 0:
        return 0

    from app.core.file_validation import safe_path_in_dir
    from app.services.cold_storage import archive_metadata, archive_original_to_cold
    from app.services.file_management_service import _file_store_lock, file_store

    cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
    archived = 0
    async with _file_store_lock:
        for file_id, info in file_store.items():
            if not isinstance(info, dict):
                continue
            if not info.get("redaction_completed_at") or info.get("original_data_purged") or info.get("cold_storage_path"):
                continue
            completed = _parse_created_at(info.get("redaction_completed_at"))
            if completed is None or completed >= cutoff:
                continue
            file_path = info.get("file_path")
            if not isinstance(file_path, str) or not safe_path_in_dir(file_path, settings.UPLOAD_DIR) or not os.path.isfile(file_path):
                continue
            try:
                cold_path = archive_original_to_cold(str(file_id), file_path)
                archive_metadata(str(file_id), {
                    "entities": info.get("entities"),
                    "entity_map": info.get("entity_map"),
                })
            except Exception:
                logger.warning("cold archive failed for %s", file_id, exc_info=True)
                continue
            next_info = dict(info)
            for field in ("content", "pages", "entities", "entity_map"):
                next_info.pop(field, None)
            next_info["original_data_purged"] = True
            next_info["cold_storage_path"] = cold_path
            next_info["cold_archived_at"] = datetime.now(UTC).isoformat()
            file_store.set(file_id, next_info)
            archived += 1
    if archived:
        logger.info("cold archive sweep: archived %d original file(s)", archived)
    return archived


async def cold_archive_loop() -> None:
    await asyncio.sleep(_COLD_ARCHIVE_FIRST_DELAY_SECONDS)
    while True:
        try:
            await cold_archive_sweep()
        except Exception:
            logger.exception("cold archive sweep failed")
        await asyncio.sleep(_COLD_ARCHIVE_INTERVAL_SECONDS)


async def retention_loop() -> None:
    await asyncio.sleep(_FIRST_SWEEP_DELAY_SECONDS)
    while True:
        try:
            await retention_sweep()
        except Exception:
            logger.exception("retention sweep failed")
        try:
            await trash_sweep()
        except Exception:
            logger.exception("trash sweep failed")
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
