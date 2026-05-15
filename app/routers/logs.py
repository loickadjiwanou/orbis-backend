"""Endpoint global pour lister les logs de tous les agents."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.middleware.auth import require_viewer
from app.models.log import Log

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("", dependencies=[Depends(require_viewer)])
async def list_logs(
    device_id: Optional[str] = None,
    plateforme: Optional[str] = None,
    level: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """
    Liste paginée des logs de tous les agents.

    Filtres :
    - device_id   : restreint à un device précis
    - plateforme  : android | linux | macos | windows
                    (résolution via la collection devices)
    - level       : DEBUG | INFO | WARNING | ERROR | CRITICAL
    - search      : regex sur le champ message
    - start_date / end_date : plage sur timestamp (ISO 8601)
    """
    query: dict = {}

    # Filtre par plateforme — résolution via la collection devices
    if plateforme:
        device_ids_for_platform = await db.devices.distinct(
            "device_id", {"plateforme": plateforme}
        )
        query["device_id"] = {"$in": device_ids_for_platform}

    # Filtre par device précis (prioritaire sur plateforme)
    if device_id:
        query["device_id"] = device_id

    if level:
        query["level"] = level.upper()

    if search:
        query["message"] = {"$regex": search, "$options": "i"}

    if start_date or end_date:
        ts_filter: dict = {}
        if start_date:
            ts_filter["$gte"] = datetime.fromisoformat(start_date)
        if end_date:
            ts_filter["$lte"] = datetime.fromisoformat(end_date)
        query["timestamp"] = ts_filter

    total = await db.logs.count_documents(query)
    cursor = db.logs.find(query).sort("timestamp", -1).skip(skip).limit(limit)
    items = [Log(**doc) async for doc in cursor]

    return {"items": items, "total": total, "skip": skip, "limit": limit}
