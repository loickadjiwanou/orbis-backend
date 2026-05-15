from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import Field
from app.models.base import OrbisBaseModel, PyObjectId


class AuditLog(OrbisBaseModel):
    id: PyObjectId = Field(default=None, alias="_id")
    operateur_id: str
    operateur_email: str
    action_type: str                        # CREATE_DEVICE / SEND_COMMAND / etc.
    device_id: Optional[str] = None
    payload: Dict[str, Any] = {}
    timestamp: datetime
    resultat: str                           # success / failure
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
