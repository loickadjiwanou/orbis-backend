from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import Field
from app.models.base import OrbisBaseModel, PyObjectId


class StepCondition(str, Enum):
    always = "always"
    on_success = "on_success"
    on_failure = "on_failure"


class TriggerType(str, Enum):
    manual = "manual"
    alert = "alert"
    schedule = "schedule"


class InstructionStep(OrbisBaseModel):
    ordre: int
    action_id: str
    parametres: Dict[str, Any] = {}
    condition_continuer: StepCondition = StepCondition.on_success
    timeout_sec: int = 60


class Instruction(OrbisBaseModel):
    id: PyObjectId = Field(default=None, alias="_id")
    nom: str
    description: str
    etapes: List[InstructionStep]
    trigger: TriggerType = TriggerType.manual
    schedule_cron: Optional[str] = None
    cree_le: datetime
    cree_par: str
    is_active: bool = True


class InstructionCreate(OrbisBaseModel):
    nom: str
    description: str
    etapes: List[InstructionStep]
    trigger: TriggerType = TriggerType.manual
    schedule_cron: Optional[str] = None


class InstructionUpdate(OrbisBaseModel):
    nom: Optional[str] = None
    description: Optional[str] = None
    etapes: Optional[List[InstructionStep]] = None
    trigger: Optional[TriggerType] = None
    schedule_cron: Optional[str] = None
    is_active: Optional[bool] = None


class InstructionExecuteRequest(OrbisBaseModel):
    """Body de POST /instructions/{id}/execute."""
    device_id: Optional[str] = None
    group_id: Optional[str] = None
