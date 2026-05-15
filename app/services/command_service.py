"""Service de gestion des commandes envoyées aux devices."""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.command import Command, CommandCreate, CommandResult, CommandStatus

logger = logging.getLogger(__name__)


async def create_command(
    db: AsyncIOMotorDatabase,
    device_id: str,
    cmd_create: CommandCreate,
    created_by: str,
) -> Command:
    """Crée une commande en MongoDB avec statut initial 'pending'."""
    now = datetime.now(timezone.utc)
    command_id = str(uuid.uuid4())
    doc = {
        "command_id": command_id,
        "device_id": device_id,
        "type": cmd_create.type,
        "payload": cmd_create.payload,
        "timeout_sec": cmd_create.timeout_sec,
        "statut": CommandStatus.pending,
        "resultat": None,
        "error_message": None,
        "exit_code": None,
        "cree_le": now,
        "envoye_le": None,
        "acquitte_le": None,
        "execute_le": None,
        "termine_le": None,
        "cree_par": created_by,
    }
    result = await db.commands.insert_one(doc)
    doc["_id"] = result.inserted_id
    return Command(**doc)


def build_mqtt_command_payload(command: Command) -> str:
    """
    Construit le payload JSON à publier sur devices/{device_id}/commands
    selon le format Session 0, Section 5.
    """
    payload = {
        "command_id": command.command_id,
        "device_id": command.device_id,
        "type": command.type,
        "payload": command.payload,
        "timeout_sec": command.timeout_sec,
        "timestamp": command.cree_le.isoformat().replace("+00:00", "Z"),
    }
    return json.dumps(payload)


async def mark_command_sent(db: AsyncIOMotorDatabase, command_id: str) -> None:
    """Met à jour le statut à 'sent' après publication MQTT réussie."""
    await db.commands.update_one(
        {"command_id": command_id},
        {"$set": {"statut": CommandStatus.sent, "envoye_le": datetime.now(timezone.utc)}},
    )


async def apply_command_result(
    db: AsyncIOMotorDatabase, result: CommandResult
) -> Optional[Command]:
    """
    Applique la mise à jour de statut reçue depuis devices/{device_id}/results.
    Suit le cycle Session 0, Section 6 : acknowledged → executing → success/failed.
    """
    statut = result.statut
    now = datetime.now(timezone.utc)
    changes: Dict[str, Any] = {"statut": statut}

    if statut == "acknowledged":
        changes["acquitte_le"] = now
    elif statut == "executing":
        changes["execute_le"] = now
    elif statut in ("success", "failed"):
        changes["termine_le"] = now
        if result.output is not None:
            changes["resultat"] = result.output[:65536]  # max 64 KB
        if result.error is not None:
            changes["error_message"] = result.error
        if result.exit_code is not None:
            changes["exit_code"] = result.exit_code

    await db.commands.update_one(
        {"command_id": result.command_id},
        {"$set": changes},
    )
    doc = await db.commands.find_one({"command_id": result.command_id})
    if doc is None:
        return None
    return Command(**doc)


async def list_commands(
    db: AsyncIOMotorDatabase,
    device_id: str,
    statut: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    """Liste paginée des commandes d'un device."""
    query: Dict[str, Any] = {"device_id": device_id}
    if statut:
        query["statut"] = statut
    total = await db.commands.count_documents(query)
    cursor = db.commands.find(query).skip(skip).limit(limit).sort("cree_le", -1)
    items = [Command(**doc) async for doc in cursor]
    return {"items": items, "total": total, "skip": skip, "limit": limit}
