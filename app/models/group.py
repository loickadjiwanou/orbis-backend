from datetime import datetime
from typing import List, Optional
from pydantic import Field
from app.models.base import OrbisBaseModel, PyObjectId


class Group(OrbisBaseModel):
    id: PyObjectId = Field(default=None, alias="_id")
    nom: str
    description: str
    device_ids: List[str] = []
    plateforme: Optional[str] = None
    cree_le: datetime
    cree_par: str


class GroupCreate(OrbisBaseModel):
    nom: str
    description: str = ""
    device_ids: List[str] = []
    plateforme: Optional[str] = None


class GroupUpdate(OrbisBaseModel):
    nom: Optional[str] = None
    description: Optional[str] = None
    plateforme: Optional[str] = None


class GroupDevicesUpdate(OrbisBaseModel):
    """Body pour ajouter/retirer des devices d'un groupe."""
    device_ids: List[str]
