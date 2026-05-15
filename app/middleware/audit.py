"""Middleware d'audit — journalise toutes les mutations POST/PATCH/DELETE."""
import logging
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.database import get_db
from app.models.user import User
from app.services.audit_service import log_audit, resolve_action_type

logger = logging.getLogger(__name__)

_AUDIT_METHODS = {"POST", "PATCH", "DELETE", "PUT"}
_SKIP_PATHS = {"/auth/login", "/auth/refresh", "/auth/logout", "/health", "/ws"}


class AuditMiddleware(BaseHTTPMiddleware):
    """
    Intercepte toutes les requêtes de mutation et crée une entrée AuditLog
    en MongoDB après chaque réponse réussie (2xx).
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        method = request.method
        path = request.url.path

        # Ne journalise que les mutations sur les endpoints métier
        if method not in _AUDIT_METHODS:
            return response
        if any(path.startswith(skip) for skip in _SKIP_PATHS):
            return response
        if not (200 <= response.status_code < 300):
            return response

        # Récupère l'utilisateur depuis le state (positionné par get_current_user)
        user: User | None = getattr(request.state, "current_user", None)
        if user is None:
            return response

        # Extrait le device_id du chemin si présent
        device_id: str | None = None
        parts = path.strip("/").split("/")
        # Pattern : /devices/{device_id}/... ou /groups/{id}/...
        if len(parts) >= 2 and parts[0] == "devices":
            device_id = parts[1]

        action_type = resolve_action_type(method, path)
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        try:
            db = get_db()
            await log_audit(
                db,
                operateur_id=str(user.id),
                operateur_email=user.email,
                action_type=action_type,
                device_id=device_id,
                payload={"method": method, "path": path},
                resultat="success",
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except Exception as exc:
            logger.error("AuditMiddleware : erreur écriture audit : %s", exc)

        return response
