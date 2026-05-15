"""Service de journalisation d'audit."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.audit import AuditLog

logger = logging.getLogger(__name__)

# Mapping (méthode HTTP, préfixe de path) → action_type
_ACTION_MAP: Dict[tuple[str, str], str] = {
    ("POST", "/devices"): "CREATE_DEVICE",
    ("DELETE", "/devices"): "REVOKE_DEVICE",
    ("PATCH", "/devices"): "UPDATE_DEVICE",
    ("POST", "/devices/{id}/command"): "SEND_COMMAND",
    ("POST", "/devices/{id}/update"): "TRIGGER_UPDATE",
    ("POST", "/groups"): "CREATE_GROUP",
    ("PATCH", "/groups"): "UPDATE_GROUP",
    ("DELETE", "/groups"): "DELETE_GROUP",
    ("POST", "/groups/{id}/command"): "GROUP_COMMAND",
    ("POST", "/actions"): "CREATE_ACTION",
    ("POST", "/actions/{id}/execute"): "EXECUTE_ACTION",
    ("POST", "/instructions"): "CREATE_INSTRUCTION",
    ("POST", "/instructions/{id}/execute"): "EXECUTE_INSTRUCTION",
    ("POST", "/alerts"): "CREATE_ALERT",
    ("DELETE", "/alerts"): "DELETE_ALERT",
    ("POST", "/agent-versions"): "UPLOAD_AGENT_VERSION",
}


def resolve_action_type(method: str, path: str) -> str:
    """Déduit l'action_type depuis la méthode HTTP et le chemin."""
    for (m, p), action in _ACTION_MAP.items():
        if m == method and path.startswith(p.split("{")[0].rstrip("/")):
            return action
    return f"{method}:{path}"


async def log_audit(
    db: AsyncIOMotorDatabase,
    *,
    operateur_id: str,
    operateur_email: str,
    action_type: str,
    device_id: Optional[str] = None,
    payload: Dict[str, Any] = None,
    resultat: str = "success",
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Persiste une entrée d'audit en MongoDB."""
    doc = {
        "operateur_id": operateur_id,
        "operateur_email": operateur_email,
        "action_type": action_type,
        "device_id": device_id,
        "payload": payload or {},
        "timestamp": datetime.now(timezone.utc),
        "resultat": resultat,
        "ip_address": ip_address,
        "user_agent": user_agent,
    }
    try:
        await db.audit_logs.insert_one(doc)
    except Exception as exc:
        logger.error("Echec écriture audit : %s", exc)


async def list_audit_logs(
    db: AsyncIOMotorDatabase,
    operateur_id: Optional[str] = None,
    action_type: Optional[str] = None,
    device_id: Optional[str] = None,
    resultat: Optional[str] = None,
    start_date=None,
    end_date=None,
    skip: int = 0,
    limit: int = 25,
) -> Dict[str, Any]:
    """Liste paginée des logs d'audit avec filtres."""
    query: Dict[str, Any] = {}
    if operateur_id:
        query["operateur_id"] = operateur_id
    if action_type:
        query["action_type"] = action_type
    if device_id:
        query["device_id"] = device_id
    if resultat:
        query["resultat"] = resultat
    if start_date or end_date:
        ts_filter: Dict[str, Any] = {}
        if start_date:
            ts_filter["$gte"] = start_date
        if end_date:
            ts_filter["$lte"] = end_date
        query["timestamp"] = ts_filter

    total = await db.audit_logs.count_documents(query)
    cursor = db.audit_logs.find(query).skip(skip).limit(limit).sort("timestamp", -1)
    items = [AuditLog(**doc) async for doc in cursor]
    return {"items": items, "total": total, "skip": skip, "limit": limit}
