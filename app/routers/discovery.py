"""Endpoint de consultation des résultats de scan réseau."""
from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.middleware.auth import require_viewer

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.get("/{device_id}", dependencies=[Depends(require_viewer)])
async def get_discovery(device_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    """Retourne le dernier résultat de scan réseau pour un device."""
    doc = await db.discovery.find_one(
        {"device_id": device_id},
        sort=[("received_at", -1)],
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Aucun scan réseau pour ce device")
    doc["_id"] = str(doc["_id"])
    return doc
