"""Endpoints de consultation des commandes."""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.middleware.auth import require_viewer
from app.models.command import Command

router = APIRouter(prefix="/commands", tags=["commands"])


@router.get("", dependencies=[Depends(require_viewer)])
async def list_all_commands(
    device_id: Optional[str] = Query(None),
    statut: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """Liste paginée de toutes les commandes (tous devices), avec filtres optionnels."""
    query: Dict[str, Any] = {}
    if device_id:
        query["device_id"] = device_id
    if statut:
        query["statut"] = statut
    if type:
        query["type"] = type
    total = await db.commands.count_documents(query)
    cursor = db.commands.find(query).skip(skip).limit(limit).sort("cree_le", -1)
    items = [Command(**doc) async for doc in cursor]
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/{command_id}", dependencies=[Depends(require_viewer)])
async def get_command(command_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> Command:
    """Retourne le détail d'une commande par son command_id UUID."""
    doc = await db.commands.find_one({"command_id": command_id})
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail="Command not found",
            headers={"code": "COMMAND_NOT_FOUND"},
        )
    return Command(**doc)
