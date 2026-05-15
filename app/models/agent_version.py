import re
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import Field, field_validator
from app.models.base import OrbisBaseModel, PyObjectId

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)


class AgentPlatform(str, Enum):
    windows = "windows"
    linux = "linux"
    macos = "macos"
    android = "android"


class AgentVersion(OrbisBaseModel):
    id: PyObjectId = Field(default=None, alias="_id")
    version: str                            # semver ex: 1.2.3
    plateforme: AgentPlatform
    url_download: str
    hash_sha256: str
    changelog: str
    date_release: datetime
    is_current: bool = False
    uploaded_by: str


class AgentVersionCreate(OrbisBaseModel):
    version: str
    plateforme: AgentPlatform
    url_download: str
    hash_sha256: str
    changelog: str
    date_release: Optional[datetime] = None

    @field_validator("url_download")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("url_download doit commencer par https:// pour garantir un transfert sécurisé")
        return v

    @field_validator("hash_sha256")
    @classmethod
    def validate_sha256(cls, v: str) -> str:
        if not _SHA256_RE.match(v):
            raise ValueError("hash_sha256 doit être une chaîne hexadécimale de 64 caractères (SHA-256)")
        return v.lower()
