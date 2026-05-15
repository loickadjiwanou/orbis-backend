"""Endpoints de gestion des versions de l'agent desktop/android."""
import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.middleware.auth import require_operator, require_viewer
from app.models.agent_version import AgentVersion, AgentVersionCreate
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent-versions", tags=["agent-versions"])


async def _get_version_or_404(db: AsyncIOMotorDatabase, version_id: str) -> dict:
    try:
        oid = ObjectId(version_id)
    except Exception:
        raise HTTPException(status_code=400, detail="version_id invalide")
    doc = await db.agent_versions.find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=404, detail="AgentVersion not found")
    return doc


@router.get("", dependencies=[Depends(require_viewer)])
async def list_versions(
    plateforme: Optional[str] = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    query = {}
    if plateforme:
        query["plateforme"] = plateforme
    items = [AgentVersion(**doc) async for doc in db.agent_versions.find(query).sort("date_release", -1)]
    return {"items": items, "total": len(items)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_version(
    body: AgentVersionCreate,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> AgentVersion:
    request.state.current_user = current_user
    now = datetime.now(timezone.utc)
    doc = {
        **body.model_dump(),
        "date_release": body.date_release or now,
        "is_current": False,
        "uploaded_by": str(current_user.id),
    }
    result = await db.agent_versions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return AgentVersion(**doc)


@router.get("/{version_id}", dependencies=[Depends(require_viewer)])
async def get_version(version_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> AgentVersion:
    return AgentVersion(**await _get_version_or_404(db, version_id))


@router.patch("/{version_id}/set-current", dependencies=[Depends(require_operator)])
async def set_current_version(
    version_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> AgentVersion:
    """Définit une version comme version courante pour sa plateforme."""
    doc = await _get_version_or_404(db, version_id)
    # Désactiver toutes les autres versions de la même plateforme
    await db.agent_versions.update_many(
        {"plateforme": doc["plateforme"]},
        {"$set": {"is_current": False}},
    )
    await db.agent_versions.update_one(
        {"_id": ObjectId(version_id)},
        {"$set": {"is_current": True}},
    )
    return AgentVersion(**await _get_version_or_404(db, version_id))


@router.delete("/{version_id}")
async def delete_version(
    version_id: str,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    request.state.current_user = current_user
    await _get_version_or_404(db, version_id)
    await db.agent_versions.delete_one({"_id": ObjectId(version_id)})
    return {"message": f"AgentVersion {version_id} deleted"}
