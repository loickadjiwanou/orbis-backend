from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import Field
from app.models.base import OrbisBaseModel, PyObjectId


class AlertConditionType(str, Enum):
    log_level = "log_level"
    inactivity = "inactivity"
    metadata_threshold = "metadata_threshold"


class AlertScope(str, Enum):
    device = "device"
    group = "group"


class AlertCondition(OrbisBaseModel):
    type: AlertConditionType
    valeur: Any
    operateur: str                          # eq / gt / lt / gte / lte / contains
    metadata_key: Optional[str] = None      # utilisé si type == metadata_threshold


class AlertHistoryEntry(OrbisBaseModel):
    triggered_at: datetime
    device_id: str
    context: Dict[str, Any]


class Alert(OrbisBaseModel):
    id: PyObjectId = Field(default=None, alias="_id")
    nom: str
    scope: AlertScope
    scope_id: str                           # device_id ou group_id
    condition: AlertCondition
    action_id: Optional[str] = None         # action automatique à déclencher
    webhook_url: Optional[str] = None
    email_recipients: List[str] = []        # destinataires des alertes email
    email_cooldown_minutes: int = 10        # délai minimum entre deux emails (0 = aucun délai)
    email_last_sent_at: Optional[datetime] = None  # géré en interne, non exposé dans Create/Update
    actif: bool = True
    derniere_declenchee: Optional[datetime] = None
    historique: List[AlertHistoryEntry] = []
    cree_le: datetime
    cree_par: str


class AlertCreate(OrbisBaseModel):
    nom: str
    scope: AlertScope
    scope_id: str
    condition: AlertCondition
    action_id: Optional[str] = None
    webhook_url: Optional[str] = None
    email_recipients: List[str] = []
    email_cooldown_minutes: int = 10


class AlertUpdate(OrbisBaseModel):
    nom: Optional[str] = None
    condition: Optional[AlertCondition] = None
    action_id: Optional[str] = None
    webhook_url: Optional[str] = None
    email_recipients: Optional[List[str]] = None
    email_cooldown_minutes: Optional[int] = None
    actif: Optional[bool] = None
