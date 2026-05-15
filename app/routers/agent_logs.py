"""Endpoint global pour les logs internes des agents Orbis."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.middleware.auth import require_viewer

router = APIRouter(prefix="/agent-logs", tags=["agent-logs"])


@router.get("", dependencies=[Depends(require_viewer)])
async def list_agent_logs(
    device_id: Optional[str] = None,
    plateforme: Optional[str] = None,
    level: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """
    Lists paginated internal agent logs (spdlog / logcat output forwarded via MQTT).
    Filters: device_id, plateforme (resolved via devices collection), level, search.
    """
    query: dict = {}

    if plateforme:
        device_ids_for_platform = await db.devices.distinct(
            "device_id", {"plateforme": plateforme}
        )
        query["device_id"] = {"$in": device_ids_for_platform}

    if device_id:
        query["device_id"] = device_id

    if level:
        query["level"] = level.upper()

    if search:
        query["message"] = {"$regex": search, "$options": "i"}

    total = await db.agent_logs.count_documents(query)
    cursor = db.agent_logs.find(query).sort("timestamp", -1).skip(skip).limit(limit)

    items = []
    async for doc in cursor:
        items.append({
            "id": str(doc["_id"]),
            "device_id": doc["device_id"],
            "timestamp": doc["timestamp"].isoformat().replace("+00:00", "Z"),
            "level": doc["level"],
            "source": doc["source"],
            "message": doc["message"],
        })

    return {"items": items, "total": total, "skip": skip, "limit": limit}
