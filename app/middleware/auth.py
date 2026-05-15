"""Dépendances FastAPI pour l'authentification JWT et le RBAC."""
import logging
from typing import List

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.models.user import User, UserRole
from app.services.auth_service import decode_token, get_user_by_id

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=True)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> User:
    """
    Dépendance FastAPI : extrait et valide le JWT, retourne l'utilisateur connecté.
    Lève HTTP 401 si le token est invalide ou expiré.
    """
    token = credentials.credentials
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise JWTError("sub manquant")
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable ou désactivé",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(roles: List[UserRole]):
    """
    Factory de dépendance RBAC.
    Exemple : Depends(require_role([UserRole.admin, UserRole.operator]))
    """
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Rôle requis : {[r.value for r in roles]}",
            )
        return current_user
    return _check


# Raccourcis communs
require_admin = require_role([UserRole.admin])
require_operator = require_role([UserRole.admin, UserRole.operator])
require_viewer = require_role([UserRole.admin, UserRole.operator, UserRole.viewer])


async def get_optional_ws_user(token: str, db: AsyncIOMotorDatabase) -> User:
    """
    Valide un JWT passé en query param WebSocket.
    Lève HTTP 401 si invalide (code 4001 côté WebSocket à gérer dans le router).
    """
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise JWTError("sub manquant")
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token WebSocket invalide",
        ) from exc

    user = await get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur WebSocket invalide",
        )
    return user
