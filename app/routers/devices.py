"""Endpoints CRUD pour les devices et leurs commandes/logs."""
import logging
from datetime import datetime
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.middleware.auth import require_operator, require_viewer
from app.models.command import Command, CommandCreate
from app.models.device import DevicePublic, DeviceUpdate
from app.models.user import User
from app.services import command_service, device_service
from app.services.emqx_service import emqx_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/devices", tags=["devices"])


@router.get(
    "",
    dependencies=[Depends(require_viewer)],
    summary="List devices",
    description=(
        "Returns a paginated list of devices. Supports filtering by group, status, platform, "
        "and a full-text search on `nom` and `hostname`.\n\n"
        "**Roles:** viewer, operator, admin"
    ),
)
async def list_devices(
    groupe_id: Optional[str] = None,
    statut: Optional[str] = None,
    plateforme: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """Liste paginée des devices avec filtres optionnels."""
    result = await device_service.list_devices(
        db, groupe_id=groupe_id, statut=statut, plateforme=plateforme,
        search=search, skip=skip, limit=limit,
    )
    return result


@router.get(
    "/{device_id}",
    dependencies=[Depends(require_viewer)],
    summary="Get device detail",
    description="Returns the full public profile of a device. The `token_hash` field is never exposed.",
    responses={404: {"description": "Device not found", "content": {"application/json": {"example": {"detail": "Device not found", "code": "DEVICE_NOT_FOUND"}}}}},
)
async def get_device(
    device_id: str, db: AsyncIOMotorDatabase = Depends(get_db)
) -> DevicePublic:
    """Retourne le détail d'un device."""
    device = await device_service.get_device_public(db, device_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
            headers={"code": "DEVICE_NOT_FOUND"},
        )
    return device


@router.patch("/{device_id}")
async def update_device(
    device_id: str,
    body: DeviceUpdate,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> DevicePublic:
    """Met à jour les champs modifiables d'un device."""
    device = await device_service.get_device_public(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found", headers={"code": "DEVICE_NOT_FOUND"})
    if device.revoked:
        raise HTTPException(status_code=400, detail="Device revoked", headers={"code": "DEVICE_REVOKED"})

    request.state.current_user = current_user
    updated = await device_service.update_device(db, device_id, body)
    return updated


@router.delete(
    "/{device_id}",
    status_code=status.HTTP_200_OK,
    summary="Revoke a device",
    description=(
        "Permanently revokes a device:\n\n"
        "1. Kicks the active MQTT connection immediately (`DELETE /api/v5/clients/{id}` on EMQX)\n"
        "2. Deletes the MQTT account from EMQX (`DELETE /api/v5/authentication/.../users/{id}`)\n"
        "3. Marks the device `revoked=true` and `statut=revoked` in MongoDB\n\n"
        "A revoked device cannot re-register and all subsequent commands will be rejected."
    ),
)
async def revoke_device(
    device_id: str,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """
    Révoque un device : déconnexion MQTT immédiate, suppression compte MQTT,
    marquage revoked=True en MongoDB.
    """
    device = await device_service.get_device_public(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found", headers={"code": "DEVICE_NOT_FOUND"})

    request.state.current_user = current_user

    # Kick + suppression compte MQTT dans EMQX
    await emqx_service.kick_client(device_id)
    await emqx_service.delete_mqtt_user(device_id)

    # Marquer révoqué en DB
    await device_service.revoke_device(db, device_id)
    logger.info("Device révoqué : %s par %s", device_id, current_user.email)
    return {"message": f"Device {device_id} revoked"}


@router.delete(
    "/{device_id}/hard",
    status_code=status.HTTP_200_OK,
    summary="Permanently delete a device",
    description=(
        "Physically removes a device from the database.\n\n"
        "1. Kicks the active MQTT connection (EMQX)\n"
        "2. Deletes the MQTT account from EMQX\n"
        "3. Deletes the device document from MongoDB entirely\n\n"
        "Unlike revoke, this operation is fully destructive — the device record is gone."
    ),
)
async def delete_device_hard(
    device_id: str,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """Supprime définitivement un device de la base de données."""
    device = await device_service.get_device_public(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found", headers={"code": "DEVICE_NOT_FOUND"})

    request.state.current_user = current_user

    # Kick + suppression compte MQTT dans EMQX
    await emqx_service.kick_client(device_id)
    await emqx_service.delete_mqtt_user(device_id)

    # Suppression physique en MongoDB
    await db.devices.delete_one({"device_id": device_id})
    logger.info("Device supprimé définitivement : %s par %s", device_id, current_user.email)
    return {"message": f"Device {device_id} permanently deleted"}


@router.post(
    "/{device_id}/command",
    status_code=status.HTTP_201_CREATED,
    summary="Send a command to a device",
    description=(
        "Creates a command record and publishes it on `devices/{device_id}/commands` via MQTT (QoS 1).\n\n"
        "**Supported types:** `shell` · `restart_service` · `collect_now` · `scan_network` · `get_info` · `agent_update`\n\n"
        "The command lifecycle (`statut`) progresses as the agent responds:\n"
        "`pending → sent → acknowledged → executing → success | failed`\n\n"
        "Each transition triggers a `command_update` WebSocket event to the dashboard."
    ),
    responses={
        503: {"description": "MQTT broker unreachable", "content": {"application/json": {"example": {"detail": "MQTT publish failed", "code": "MQTT_PUBLISH_FAILED"}}}},
    },
)
async def send_command(
    device_id: str,
    body: CommandCreate,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Command:
    """Crée et envoie une commande à un device via MQTT."""
    from app.mqtt.client import publish

    device = await device_service.get_device_public(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found", headers={"code": "DEVICE_NOT_FOUND"})
    if device.revoked:
        raise HTTPException(status_code=400, detail="Device revoked", headers={"code": "DEVICE_REVOKED"})

    request.state.current_user = current_user

    command = await command_service.create_command(db, device_id, body, str(current_user.id))

    # Construction et publication du payload MQTT (Session 0, Section 5)
    mqtt_payload = command_service.build_mqtt_command_payload(command)
    topic = f"devices/{device_id}/commands"
    try:
        await publish(topic, mqtt_payload, qos=1, retain=False)
        await command_service.mark_command_sent(db, command.command_id)
    except Exception as exc:
        logger.error("Echec publication commande device=%s : %s", device_id, exc)
        raise HTTPException(
            status_code=503,
            detail="MQTT publish failed",
            headers={"code": "MQTT_PUBLISH_FAILED"},
        ) from exc

    # Relire pour avoir statut "sent"
    doc = await db.commands.find_one({"command_id": command.command_id})
    return Command(**doc)


@router.post("/{device_id}/update", status_code=status.HTTP_201_CREATED)
async def trigger_update(
    device_id: str,
    body: dict,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Command:
    """
    Déclenche une mise à jour de l'agent via la commande agent_update.
    body : { "version_id": "<AgentVersion ObjectId>" }
    """
    from app.mqtt.client import publish

    version_id = body.get("version_id")
    if not version_id:
        raise HTTPException(status_code=400, detail="version_id requis")

    version_doc = await db.agent_versions.find_one({"_id": ObjectId(version_id)})
    if version_doc is None:
        raise HTTPException(status_code=404, detail="AgentVersion not found")

    device = await device_service.get_device_public(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found", headers={"code": "DEVICE_NOT_FOUND"})

    request.state.current_user = current_user

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
        await publish(f"devices/{device_id}/update", mqtt_payload, qos=1)  # alias topic
        await command_service.mark_command_sent(db, command.command_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MQTT publish failed", headers={"code": "MQTT_PUBLISH_FAILED"}) from exc

    doc = await db.commands.find_one({"command_id": command.command_id})
    return Command(**doc)


@router.get(
    "/{device_id}/logs",
    dependencies=[Depends(require_viewer)],
    summary="Query device logs",
    description=(
        "Returns a paginated list of log entries for a device, newest first.\n\n"
        "Supports filtering by `level`, exact `source` match, `search` (regex on `message`), "
        "and a date range on `timestamp`.\n\n"
        "Logs are retained for **90 days** (configurable via `LOG_RETENTION_DAYS`)."
    ),
)
async def get_device_logs(
    device_id: str,
    level: Optional[str] = None,
    source: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """Liste paginée des logs d'un device avec filtres."""
    from app.models.log import Log

    query: dict = {"device_id": device_id}
    if level:
        query["level"] = level.upper()
    if source:
        query["source"] = source
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
    cursor = db.logs.find(query).skip(skip).limit(limit).sort("timestamp", -1)
    items = [Log(**doc) async for doc in cursor]
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/{device_id}/commands", dependencies=[Depends(require_viewer)])
async def get_device_commands(
    device_id: str,
    statut: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """Liste paginée des commandes d'un device."""
    return await command_service.list_commands(db, device_id, statut=statut, skip=skip, limit=limit)
