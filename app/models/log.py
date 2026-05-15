from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import Field, field_validator
from app.models.base import OrbisBaseModel, PyObjectId


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Log(OrbisBaseModel):
    id: PyObjectId = Field(default=None, alias="_id")
    device_id: str
    timestamp: datetime
    level: LogLevel
    source: str
    message: str
    metadata: Dict[str, Any] = {}
    received_at: datetime


class LogIncoming(OrbisBaseModel):
    """Format d'un log reçu via MQTT (session 0, Section 4)."""
    device_id: str
    timestamp: datetime
    level: LogLevel
    source: str
    message: str
    metadata: Dict[str, Any] = {}

    @field_validator("source")
    @classmethod
    def source_not_empty(cls, v: str) -> str:
        if not v or len(v) > 100:
            raise ValueError("source doit être non vide et <= 100 caractères")
        return v

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v or len(v) > 10000:
            raise ValueError("message doit être non vide et <= 10 000 caractères")
        return v
