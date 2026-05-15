"""Tests du cycle de vie des commandes (Session 0, Section 6)."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from app.models.command import CommandCreate, CommandResult, CommandStatus
from app.services.command_service import (
    apply_command_result,
    create_command,
    mark_command_sent,
)


def _make_mock_db_with_command(device_id: str, command_id: str, statut: str) -> MagicMock:
    """Crée un mock DB avec une commande existante."""
    db = MagicMock()
    cmd_doc = {
        "_id": ObjectId(),
        "command_id": command_id,
        "device_id": device_id,
        "type": "shell",
        "payload": {"command": "df -h"},
        "timeout_sec": 30,
        "statut": statut,
        "resultat": None,
        "error_message": None,
        "exit_code": None,
        "cree_le": datetime.now(timezone.utc),
        "envoye_le": None,
        "acquitte_le": None,
        "execute_le": None,
        "termine_le": None,
        "cree_par": "user_id",
    }
    db.commands.insert_one = AsyncMock(return_value=MagicMock(inserted_id=cmd_doc["_id"]))
    db.commands.find_one = AsyncMock(return_value=cmd_doc)
    db.commands.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    return db, cmd_doc


# ---------------------------------------------------------------------------
# Test : cycle pending → sent → acknowledged → executing → success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_command_lifecycle_pending_to_success():
    """Vérifie le cycle complet : pending → sent → acknowledged → executing → success."""
    device_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    command_id = str(uuid.uuid4())
    db, cmd_doc = _make_mock_db_with_command(device_id, command_id, "sent")

    # 1. acknowledged
    result_ack = CommandResult(
        command_id=command_id,
        device_id=device_id,
        statut="acknowledged",
        output=None,
        error=None,
        exit_code=None,
        timestamp=datetime.now(timezone.utc),
    )
    cmd_doc["statut"] = "acknowledged"
    cmd_doc["acquitte_le"] = datetime.now(timezone.utc)
    command = await apply_command_result(db, result_ack)
    assert command is not None
    assert command.command_id == command_id

    # 2. executing
    result_exec = CommandResult(
        command_id=command_id,
        device_id=device_id,
        statut="executing",
        output=None,
        error=None,
        exit_code=None,
        timestamp=datetime.now(timezone.utc),
    )
    cmd_doc["statut"] = "executing"
    cmd_doc["execute_le"] = datetime.now(timezone.utc)
    command = await apply_command_result(db, result_exec)
    assert command is not None

    # 3. success
    result_ok = CommandResult(
        command_id=command_id,
        device_id=device_id,
        statut="success",
        output="Filesystem      Size  Used Avail Use% Mounted on\n/dev/sda1       100G   45G   55G  45% /\n",
        error=None,
        exit_code=0,
        timestamp=datetime.now(timezone.utc),
    )
    cmd_doc["statut"] = "success"
    cmd_doc["resultat"] = result_ok.output
    cmd_doc["exit_code"] = 0
    cmd_doc["termine_le"] = datetime.now(timezone.utc)
    command = await apply_command_result(db, result_ok)
    assert command is not None
    assert command.statut == CommandStatus.success

    # Vérifie que update_one a bien été appelé pour chaque transition
    assert db.commands.update_one.call_count == 3


# ---------------------------------------------------------------------------
# Test : cycle pending → sent → acknowledged → executing → failed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_command_lifecycle_pending_to_failed():
    """Vérifie le cycle d'échec : acknowledged → executing → failed."""
    device_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    command_id = str(uuid.uuid4())
    db, cmd_doc = _make_mock_db_with_command(device_id, command_id, "sent")

    # acknowledged
    result_ack = CommandResult(
        command_id=command_id,
        device_id=device_id,
        statut="acknowledged",
        output=None, error=None, exit_code=None,
        timestamp=datetime.now(timezone.utc),
    )
    cmd_doc["statut"] = "acknowledged"
    await apply_command_result(db, result_ack)

    # failed directement depuis executing
    result_fail = CommandResult(
        command_id=command_id,
        device_id=device_id,
        statut="failed",
        output="nginx: unrecognized service\n",
        error="Command exited with code 1",
        exit_code=1,
        timestamp=datetime.now(timezone.utc),
    )
    cmd_doc["statut"] = "failed"
    cmd_doc["error_message"] = result_fail.error
    cmd_doc["exit_code"] = 1
    cmd_doc["termine_le"] = datetime.now(timezone.utc)
    command = await apply_command_result(db, result_fail)

    assert command is not None
    assert command.statut == CommandStatus.failed
    assert command.exit_code == 1


# ---------------------------------------------------------------------------
# Test : create_command génère un command_id UUID valide
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_command_generates_uuid():
    """create_command doit générer un command_id UUID v4 valide."""
    device_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    db = MagicMock()
    inserted_id = ObjectId()
    db.commands.insert_one = AsyncMock(return_value=MagicMock(inserted_id=inserted_id))

    cmd_create = CommandCreate(type="shell", payload={"command": "ls -la"}, timeout_sec=30)
    command = await create_command(db, device_id, cmd_create, "user_id")

    assert command.device_id == device_id
    assert command.type == "shell"
    assert command.statut == CommandStatus.pending
    assert len(command.command_id) == 36  # UUID v4 : 36 caractères avec tirets

    # Vérification UUID valide
    parsed = uuid.UUID(command.command_id)
    assert parsed.version == 4


# ---------------------------------------------------------------------------
# Test : build_mqtt_command_payload respecte Session 0, Section 5
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_mqtt_payload_format():
    """Le payload MQTT doit respecter exactement le format Session 0, Section 5."""
    import json
    from app.models.command import Command, CommandStatus
    from app.services.command_service import build_mqtt_command_payload

    command = Command(
        _id=str(ObjectId()),
        command_id="c1d2e3f4-a5b6-7890-cdef-123456789abc",
        device_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        type="shell",
        payload={"command": "systemctl restart nginx", "shell": "/bin/bash"},
        timeout_sec=30,
        statut=CommandStatus.pending,
        resultat=None,
        error_message=None,
        exit_code=None,
        cree_le=datetime.now(timezone.utc),
        envoye_le=None,
        acquitte_le=None,
        execute_le=None,
        termine_le=None,
        cree_par="user_id",
    )

    payload_str = build_mqtt_command_payload(command)
    payload = json.loads(payload_str)

    # Vérification des champs obligatoires (Session 0, Section 5)
    assert payload["command_id"] == "c1d2e3f4-a5b6-7890-cdef-123456789abc"
    assert payload["device_id"] == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    assert payload["type"] == "shell"
    assert payload["payload"]["command"] == "systemctl restart nginx"
    assert payload["timeout_sec"] == 30
    assert "timestamp" in payload
    assert payload["timestamp"].endswith("Z")
