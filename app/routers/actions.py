"""Endpoints CRUD pour les actions et leur exécution."""
import logging
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.middleware.auth import require_operator, require_viewer
from app.models.action import Action, ActionCreate, ActionExecuteRequest, ActionUpdate
from app.models.command import CommandCreate
from app.models.user import User
from app.services import command_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/actions", tags=["actions"])


async def _get_action_or_404(db: AsyncIOMotorDatabase, action_id: str) -> dict:
    try:
        oid = ObjectId(action_id)
    except Exception:
        raise HTTPException(status_code=400, detail="action_id invalide")
    doc = await db.actions.find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=404, detail="Action not found")
    return doc


def _resolve_script(template: str, params: dict) -> str:
    """Remplace les placeholders {{param}} dans le template par les valeurs."""
    for key, value in params.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template


@router.get("", dependencies=[Depends(require_viewer)])
async def list_actions(db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    items = [Action(**doc) async for doc in db.actions.find({"is_active": True})]
    return {"items": items, "total": len(items)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_action(
    body: ActionCreate,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Action:
    request.state.current_user = current_user
    now = datetime.now(timezone.utc)
    doc = {**body.model_dump(), "cree_le": now, "cree_par": str(current_user.id), "is_active": True}
    result = await db.actions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return Action(**doc)


@router.get("/{action_id}", dependencies=[Depends(require_viewer)])
async def get_action(action_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> Action:
    return Action(**await _get_action_or_404(db, action_id))


@router.patch("/{action_id}", dependencies=[Depends(require_operator)])
async def update_action(
    action_id: str,
    body: ActionUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Action:
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    await db.actions.update_one({"_id": ObjectId(action_id)}, {"$set": changes})
    return Action(**await _get_action_or_404(db, action_id))


@router.delete("/{action_id}", dependencies=[Depends(require_operator)])
async def delete_action(
    action_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    await _get_action_or_404(db, action_id)
    await db.actions.update_one({"_id": ObjectId(action_id)}, {"$set": {"is_active": False}})
    return {"message": f"Action {action_id} désactivée"}


@router.post("/{action_id}/execute", status_code=status.HTTP_201_CREATED)
async def execute_action(
    action_id: str,
    body: ActionExecuteRequest,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """Exécute une action sur un device ou un groupe."""
    from app.mqtt.client import publish

    action_doc = await _get_action_or_404(db, action_id)
    request.state.current_user = current_user

    script = _resolve_script(action_doc["script_template"], body.parametres)
    cmd_create = CommandCreate(type="shell", payload={"command": script, "shell": "/bin/bash"})

    target_devices: list[str] = []
    if body.device_id:
        target_devices = [body.device_id]
    elif body.group_id:
        group_doc = await db.groups.find_one({"_id": ObjectId(body.group_id)})
        if group_doc:
            target_devices = group_doc.get("device_ids", [])

    results = []
    for device_id in target_devices:
        command = await command_service.create_command(db, device_id, cmd_create, str(current_user.id))
        mqtt_payload = command_service.build_mqtt_command_payload(command)
        try:
            await publish(f"devices/{device_id}/commands", mqtt_payload, qos=1)
            await command_service.mark_command_sent(db, command.command_id)
            results.append({"device_id": device_id, "command_id": command.command_id, "status": "sent"})
        except Exception as exc:
            results.append({"device_id": device_id, "status": "failed", "error": str(exc)})

    return {"action_id": action_id, "results": results}
