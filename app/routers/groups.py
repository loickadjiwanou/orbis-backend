"""Endpoints CRUD pour les groupes de devices."""
import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.middleware.auth import require_operator, require_viewer
from app.models.command import CommandCreate
from app.models.group import Group, GroupCreate, GroupDevicesUpdate, GroupUpdate
from app.models.user import User
from app.services import command_service, device_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/groups", tags=["groups"])


async def _get_group_or_404(db: AsyncIOMotorDatabase, group_id: str) -> dict:
    try:
        oid = ObjectId(group_id)
    except Exception:
        raise HTTPException(status_code=400, detail="group_id invalide")
    doc = await db.groups.find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return doc


@router.get("", dependencies=[Depends(require_viewer)])
async def list_groups(db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    items = [Group(**doc) async for doc in db.groups.find()]
    return {"items": items, "total": len(items)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupCreate,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Group:
    request.state.current_user = current_user
    now = datetime.now(timezone.utc)
    doc = {**body.model_dump(), "cree_le": now, "cree_par": str(current_user.id)}
    result = await db.groups.insert_one(doc)
    doc["_id"] = result.inserted_id
    return Group(**doc)


@router.get("/{group_id}", dependencies=[Depends(require_viewer)])
async def get_group(group_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> Group:
    return Group(**await _get_group_or_404(db, group_id))


@router.patch("/{group_id}")
async def update_group(
    group_id: str,
    body: GroupUpdate,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Group:
    request.state.current_user = current_user
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    await db.groups.update_one({"_id": ObjectId(group_id)}, {"$set": changes})
    return Group(**await _get_group_or_404(db, group_id))


@router.delete("/{group_id}")
async def delete_group(
    group_id: str,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    request.state.current_user = current_user
    await _get_group_or_404(db, group_id)
    await db.groups.delete_one({"_id": ObjectId(group_id)})
    return {"message": f"Group {group_id} deleted"}


@router.post("/{group_id}/devices", dependencies=[Depends(require_operator)])
async def add_devices_to_group(
    group_id: str,
    body: GroupDevicesUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Group:
    """Ajoute des devices à un groupe (union) en validant la plateforme."""
    group_doc = await _get_group_or_404(db, group_id)
    group_plateforme: Optional[str] = group_doc.get("plateforme")

    # Fetch devices being added to validate/infer platform
    if body.device_ids:
        devices = await db.devices.find(
            {"device_id": {"$in": body.device_ids}},
            {"device_id": 1, "plateforme": 1},
        ).to_list(length=None)

        # Infer plateforme from first device if group has none yet
        if group_plateforme is None and devices:
            group_plateforme = devices[0].get("plateforme")
            await db.groups.update_one(
                {"_id": ObjectId(group_id)},
                {"$set": {"plateforme": group_plateforme}},
            )

        # Validate all devices share the same platform as the group
        for device in devices:
            if device.get("plateforme") != group_plateforme:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="All devices must share the same platform as the group",
                )

    await db.groups.update_one(
        {"_id": ObjectId(group_id)},
        {"$addToSet": {"device_ids": {"$each": body.device_ids}}},
    )
    # Mise à jour réciproque des devices
    await db.devices.update_many(
        {"device_id": {"$in": body.device_ids}},
        {"$addToSet": {"groupe_ids": group_id}},
    )
    return Group(**await _get_group_or_404(db, group_id))


@router.delete("/{group_id}/devices", dependencies=[Depends(require_operator)])
async def remove_devices_from_group(
    group_id: str,
    body: GroupDevicesUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Group:
    """Retire des devices d'un groupe."""
    await _get_group_or_404(db, group_id)
    await db.groups.update_one(
        {"_id": ObjectId(group_id)},
        {"$pull": {"device_ids": {"$in": body.device_ids}}},
    )
    await db.devices.update_many(
        {"device_id": {"$in": body.device_ids}},
        {"$pull": {"groupe_ids": group_id}},
    )
    return Group(**await _get_group_or_404(db, group_id))


@router.post("/{group_id}/update")
async def group_update(
    group_id: str,
    body: dict,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """Déclenche une mise à jour de l'agent sur tous les devices d'un groupe."""
    from app.mqtt.client import publish
    from bson import ObjectId as ObjId

    version_id = body.get("version_id")
    if not version_id:
        raise HTTPException(status_code=400, detail="version_id requis")

    try:
        version_doc = await db.agent_versions.find_one({"_id": ObjId(version_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="version_id invalide")
    if version_doc is None:
        raise HTTPException(status_code=404, detail="AgentVersion not found")

    group_doc = await _get_group_or_404(db, group_id)
    request.state.current_user = current_user

    results = []
    for device_id in group_doc.get("device_ids", []):
        cmd_create = CommandCreate(
            type="agent_update",
            payload={
                "version": version_doc["version"],
                "url": version_doc["url_download"],
                "sha256": version_doc["hash_sha256"],
                "changelog": version_doc["changelog"],
            },
            timeout_sec=300,
        )
        command = await command_service.create_command(db, device_id, cmd_create, str(current_user.id))
        mqtt_payload = command_service.build_mqtt_command_payload(command)
        try:
            await publish(f"devices/{device_id}/commands", mqtt_payload, qos=1)
            await publish(f"devices/{device_id}/update", mqtt_payload, qos=1)
            await command_service.mark_command_sent(db, command.command_id)
            results.append({"device_id": device_id, "command_id": command.command_id, "status": "sent"})
        except Exception as exc:
            logger.error("Echec update groupe device=%s : %s", device_id, exc)
            results.append({"device_id": device_id, "command_id": None, "status": "failed", "error": str(exc)})

    return {"results": results, "version": version_doc["version"]}


@router.post("/{group_id}/command")
async def group_command(
    group_id: str,
    body: CommandCreate,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """Envoie une commande à tous les devices d'un groupe."""
    from app.mqtt.client import publish

    group_doc = await _get_group_or_404(db, group_id)
    request.state.current_user = current_user
    results = []
    for device_id in group_doc.get("device_ids", []):
        command = await command_service.create_command(db, device_id, body, str(current_user.id))
        mqtt_payload = command_service.build_mqtt_command_payload(command)
        try:
            await publish(f"devices/{device_id}/commands", mqtt_payload, qos=1)
            await command_service.mark_command_sent(db, command.command_id)
            results.append({"device_id": device_id, "command_id": command.command_id, "status": "sent"})
        except Exception as exc:
            logger.error("Echec commande groupe device=%s : %s", device_id, exc)
            results.append({"device_id": device_id, "status": "failed", "error": str(exc)})
    return {"results": results}
