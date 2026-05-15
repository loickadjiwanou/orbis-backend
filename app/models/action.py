from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import Field
from app.models.base import OrbisBaseModel, PyObjectId


class ActionParameter(OrbisBaseModel):
    nom: str
    type: str                               # string / int / bool / enum
    requis: bool = True
    valeur_defaut: Optional[Any] = None
    description: str
    enum_values: Optional[List[str]] = None


class Action(OrbisBaseModel):
    id: PyObjectId = Field(default=None, alias="_id")
    nom: str
    description: str
    type: str                               # shell / api / script / system
    script_template: str                    # template avec {{param}} placeholders
    parametres: List[ActionParameter] = []
    compatible_plateformes: List[str] = ["windows", "linux", "macos", "android"]
    cree_le: datetime
    cree_par: str
    is_active: bool = True


class ActionCreate(OrbisBaseModel):
    nom: str
    description: str
    type: str
    script_template: str
    parametres: List[ActionParameter] = []
    compatible_plateformes: List[str] = ["windows", "linux", "macos", "android"]


class ActionUpdate(OrbisBaseModel):
    nom: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    script_template: Optional[str] = None
    parametres: Optional[List[ActionParameter]] = None
    compatible_plateformes: Optional[List[str]] = None
    is_active: Optional[bool] = None


class ActionExecuteRequest(OrbisBaseModel):
    """Body de POST /actions/{id}/execute."""
    device_id: Optional[str] = None
    group_id: Optional[str] = None
    parametres: Dict[str, Any] = {}
