from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import Field
from app.models.base import OrbisBaseModel, PyObjectId


class DeviceStatus(str, Enum):
    online = "online"
    offline = "offline"
    unknown = "unknown"
    revoked = "revoked"


class LogConfig(OrbisBaseModel):
    interval_sec: int = 60
    levels: List[str] = ["INFO", "WARNING", "ERROR", "CRITICAL"]
    sources: List[str] = []


class Device(OrbisBaseModel):
    id: PyObjectId = Field(default=None, alias="_id")
    device_id: str                          # UUID v4 généré par l'agent
    nom: str
    hostname: str
    plateforme: str                         # windows / linux / macos / android
    os_version: str
    architecture: str                       # x86_64 / arm64 / armv7 / etc.
    statut: DeviceStatus = DeviceStatus.unknown
    version_agent: str
    derniere_connexion: Optional[datetime] = None
    config_logs: LogConfig = Field(default_factory=LogConfig)
    groupe_ids: List[str] = []
    token_hash: str                         # bcrypt hash du token MQTT
    revoked: bool = False
    created_at: datetime
    metadata: Dict[str, Any] = {}


class DeviceCreate(OrbisBaseModel):
    """Payload de création manuelle d'un device (avant onboarding MQTT)."""
    nom: str
    device_id: str
    hostname: str = ""
    plateforme: str = "linux"
    os_version: str = ""
    architecture: str = "x86_64"
    version_agent: str = "1.0.0"


class DeviceUpdate(OrbisBaseModel):
    nom: Optional[str] = None
    config_logs: Optional[LogConfig] = None
    groupe_ids: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class DevicePublic(OrbisBaseModel):
    """Device sans token_hash — à retourner dans les réponses API."""
    id: PyObjectId = Field(default=None, alias="_id")
    device_id: str
    nom: str
    hostname: str
    plateforme: str
    os_version: str
    architecture: str
    statut: DeviceStatus
    version_agent: str
    derniere_connexion: Optional[datetime] = None
    config_logs: LogConfig
    groupe_ids: List[str]
    revoked: bool
    created_at: datetime
    metadata: Dict[str, Any]
    # Métriques temps réel issues du heartbeat (stockées directement dans le document)
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None
    disk_percent: Optional[float] = None
    storage_percent: Optional[float] = None
    battery_level: Optional[float] = None
    battery_charging: Optional[bool] = None
    network_type: Optional[str] = None
    uptime_sec: Optional[int] = None
