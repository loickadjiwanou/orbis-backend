"""Service CRUD pour les devices MongoDB."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.device import Device, DevicePublic, DeviceStatus, DeviceUpdate

logger = logging.getLogger(__name__)


def _doc_to_public(doc: dict) -> DevicePublic:
    """Convertit un document MongoDB en DevicePublic (sans token_hash)."""
    doc.pop("token_hash", None)
    return DevicePublic(**doc)


async def get_device_by_device_id(
    db: AsyncIOMotorDatabase, device_id: str
) -> Optional[Device]:
    """Récupère le Device complet (avec token_hash) par son device_id UUID."""
    doc = await db.devices.find_one({"device_id": device_id})
    if doc is None:
        return None
    return Device(**doc)


async def get_device_public(
    db: AsyncIOMotorDatabase, device_id: str
) -> Optional[DevicePublic]:
    """Récupère le DevicePublic par device_id."""
    doc = await db.devices.find_one({"device_id": device_id})
    if doc is None:
        return None
    return _doc_to_public(doc)


async def list_devices(
    db: AsyncIOMotorDatabase,
    groupe_id: Optional[str] = None,
    statut: Optional[str] = None,
    plateforme: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    """Liste paginée des devices avec filtres optionnels."""
    query: Dict[str, Any] = {}
    if groupe_id:
        query["groupe_ids"] = groupe_id
    if statut:
        query["statut"] = statut
    if plateforme:
        query["plateforme"] = plateforme
    if search:
        query["$or"] = [
            {"nom": {"$regex": search, "$options": "i"}},
            {"hostname": {"$regex": search, "$options": "i"}},
        ]

    total = await db.devices.count_documents(query)
    cursor = db.devices.find(query).skip(skip).limit(limit).sort("created_at", -1)
    items = []
    async for doc in cursor:
        items.append(_doc_to_public(doc))
    return {"items": items, "total": total, "skip": skip, "limit": limit}


async def update_device(
    db: AsyncIOMotorDatabase, device_id: str, update: DeviceUpdate
) -> Optional[DevicePublic]:
    """Met à jour les champs modifiables d'un device."""
    changes = {k: v for k, v in update.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        return await get_device_public(db, device_id)
    await db.devices.update_one({"device_id": device_id}, {"$set": changes})
    return await get_device_public(db, device_id)


async def revoke_device(
    db: AsyncIOMotorDatabase, device_id: str
) -> bool:
    """Marque un device comme révoqué et offline."""
    result = await db.devices.update_one(
        {"device_id": device_id},
        {"$set": {"revoked": True, "statut": DeviceStatus.revoked}},
    )
    return result.modified_count > 0


async def update_device_status(
    db: AsyncIOMotorDatabase,
    device_id: str,
    statut: DeviceStatus,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Met à jour le statut et la dernière connexion d'un device."""
    changes: Dict[str, Any] = {
        "statut": statut,
        "derniere_connexion": datetime.now(timezone.utc),
    }
    if extra:
        changes.update(extra)
    await db.devices.update_one({"device_id": device_id}, {"$set": changes})


async def mark_inactive_devices_offline(db: AsyncIOMotorDatabase, timeout_sec: int = 300) -> int:
    """
    Passe à 'offline' tous les devices online sans heartbeat depuis timeout_sec.
    Retourne le nombre de devices mis à jour.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_sec)
    result = await db.devices.update_many(
        {
            "statut": DeviceStatus.online,
            "derniere_connexion": {"$lt": cutoff},
            "revoked": False,
        },
        {"$set": {"statut": DeviceStatus.offline}},
    )
    if result.modified_count:
        logger.info("%d device(s) passé(s) offline (inactivité)", result.modified_count)
    return result.modified_count
