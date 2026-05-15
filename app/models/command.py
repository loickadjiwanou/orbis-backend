from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import Field
from app.models.base import OrbisBaseModel, PyObjectId


class CommandStatus(str, Enum):
    pending = "pending"
    sent = "sent"
    acknowledged = "acknowledged"
    executing = "executing"
    success = "success"
    failed = "failed"


class Command(OrbisBaseModel):
    id: PyObjectId = Field(default=None, alias="_id")
    command_id: str                         # UUID v4 unique de la commande
    device_id: str
    type: str                               # shell / restart_service / collect_now / scan_network / get_info / agent_update
    payload: Dict[str, Any]
    timeout_sec: int = 30
    statut: CommandStatus = CommandStatus.pending
    resultat: Optional[str] = None          # stdout final (max 64 KB)
    error_message: Optional[str] = None
    exit_code: Optional[int] = None
    cree_le: datetime
    envoye_le: Optional[datetime] = None
    acquitte_le: Optional[datetime] = None
    execute_le: Optional[datetime] = None
    termine_le: Optional[datetime] = None
    cree_par: str                           # user_id de l'opérateur


class CommandCreate(OrbisBaseModel):
    """Body de la requête POST /devices/{id}/command."""
    type: str
    payload: Dict[str, Any] = {}
    timeout_sec: int = 30


class CommandResult(OrbisBaseModel):
    """Payload reçu sur devices/{device_id}/results (session 0, Section 6)."""
    command_id: str
    device_id: str
    statut: str                             # acknowledged | executing | success | failed
    output: Optional[str] = None
    error: Optional[str] = None
    exit_code: Optional[int] = None
    timestamp: datetime
