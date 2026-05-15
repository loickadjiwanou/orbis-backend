from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import Field
from app.models.base import OrbisBaseModel, PyObjectId


class UserRole(str, Enum):
    admin = "admin"
    operator = "operator"
    viewer = "viewer"


class User(OrbisBaseModel):
    id: PyObjectId = Field(default=None, alias="_id")
    email: str
    hashed_password: str
    full_name: str
    role: UserRole
    is_active: bool = True
    created_at: datetime
    last_login: Optional[datetime] = None


class UserPublic(OrbisBaseModel):
    """User sans le hash du mot de passe — à retourner dans les réponses API."""
    id: PyObjectId = Field(default=None, alias="_id")
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime] = None


class UserCreate(OrbisBaseModel):
    email: str
    password: str
    full_name: str
    role: UserRole = UserRole.viewer


class UserUpdate(OrbisBaseModel):
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None
