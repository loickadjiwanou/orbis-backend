"""Endpoints CRUD pour les instructions (workflows multi-étapes)."""
import logging
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.middleware.auth import require_operator, require_viewer
from app.models.instruction import (
    Instruction,
    InstructionCreate,
    InstructionExecuteRequest,
    InstructionUpdate,
    StepCondition,
)
from app.models.command import CommandCreate
from app.models.user import User
from app.services import command_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/instructions", tags=["instructions"])


async def _get_instruction_or_404(db: AsyncIOMotorDatabase, instruction_id: str) -> dict:
    try:
        oid = ObjectId(instruction_id)
    except Exception:
        raise HTTPException(status_code=400, detail="instruction_id invalide")
    doc = await db.instructions.find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=404, detail="Instruction not found")
    return doc


@router.get("", dependencies=[Depends(require_viewer)])
async def list_instructions(db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    items = [Instruction(**doc) async for doc in db.instructions.find({"is_active": True})]
    return {"items": items, "total": len(items)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_instruction(
    body: InstructionCreate,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Instruction:
    request.state.current_user = current_user
    now = datetime.now(timezone.utc)
    doc = {**body.model_dump(), "cree_le": now, "cree_par": str(current_user.id), "is_active": True}
    result = await db.instructions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return Instruction(**doc)


@router.get("/{instruction_id}", dependencies=[Depends(require_viewer)])
async def get_instruction(instruction_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> Instruction:
    return Instruction(**await _get_instruction_or_404(db, instruction_id))


@router.patch("/{instruction_id}", dependencies=[Depends(require_operator)])
async def update_instruction(
    instruction_id: str,
    body: InstructionUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Instruction:
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    await db.instructions.update_one({"_id": ObjectId(instruction_id)}, {"$set": changes})
    return Instruction(**await _get_instruction_or_404(db, instruction_id))


@router.delete("/{instruction_id}", dependencies=[Depends(require_operator)])
async def delete_instruction(
    instruction_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    await _get_instruction_or_404(db, instruction_id)
    await db.instructions.update_one(
        {"_id": ObjectId(instruction_id)}, {"$set": {"is_active": False}}
    )
    return {"message": f"Instruction {instruction_id} désactivée"}


@router.post("/{instruction_id}/execute", status_code=status.HTTP_202_ACCEPTED)
async def execute_instruction(
    instruction_id: str,
    body: InstructionExecuteRequest,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """
    Exécute séquentiellement les étapes d'une instruction sur un device ou groupe.
    Chaque étape résout son action et envoie une commande shell.
    """
    from app.mqtt.client import publish

    instruction_doc = await _get_instruction_or_404(db, instruction_id)
    request.state.current_user = current_user

    target_devices: list[str] = []
    if body.device_id:
        target_devices = [body.device_id]
    elif body.group_id:
        group_doc = await db.groups.find_one({"_id": ObjectId(body.group_id)})
        if group_doc:
            target_devices = group_doc.get("device_ids", [])

    etapes = sorted(instruction_doc.get("etapes", []), key=lambda e: e["ordre"])
    results = {"instruction_id": instruction_id, "devices": {}}

    for device_id in target_devices:
        step_results = []
        for etape in etapes:
            action_doc = await db.actions.find_one({"_id": ObjectId(etape["action_id"])})
            if not action_doc:
                step_results.append({"step": etape["ordre"], "status": "skipped", "reason": "action not found"})
                continue

            cmd_create = CommandCreate(
                type="shell",
                payload={"command": action_doc["script_template"], "shell": "/bin/bash"},
                timeout_sec=etape.get("timeout_sec", 60),
            )
            command = await command_service.create_command(db, device_id, cmd_create, str(current_user.id))
            try:
                await publish(f"devices/{device_id}/commands", command_service.build_mqtt_command_payload(command), qos=1)
                await command_service.mark_command_sent(db, command.command_id)
                step_results.append({"step": etape["ordre"], "command_id": command.command_id, "status": "sent"})
            except Exception as exc:
                step_results.append({"step": etape["ordre"], "status": "failed", "error": str(exc)})
                if etape.get("condition_continuer") == StepCondition.on_success:
                    break

        results["devices"][device_id] = step_results

    return results
