"""Endpoints CRUD pour les alertes."""
import logging
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.middleware.auth import require_operator, require_viewer
from app.models.alert import Alert, AlertCreate, AlertUpdate
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])


async def _get_alert_or_404(db: AsyncIOMotorDatabase, alert_id: str) -> dict:
    try:
        oid = ObjectId(alert_id)
    except Exception:
        raise HTTPException(status_code=400, detail="alert_id invalide")
    doc = await db.alerts.find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return doc


@router.get("", dependencies=[Depends(require_viewer)])
async def list_alerts(db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    items = [Alert(**doc) async for doc in db.alerts.find()]
    return {"items": items, "total": len(items)}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create an alert rule",
    description=(
        "Creates an alert that evaluates every incoming log for the target device or group.\n\n"
        "**Condition types:**\n"
        "- `log_level` — fires when a log with the specified level is received (`operateur: eq`)\n"
        "- `metadata_threshold` — fires when `log.metadata[metadata_key]` satisfies the comparison "
        "(`operateur`: `eq` `gt` `lt` `gte` `lte` `contains`)\n"
        "- `inactivity` — checked by a background job every 60 s (not triggered by log events)\n\n"
        "**On trigger:** optionally executes an `action_id` and/or POSTs to `webhook_url`."
    ),
)
async def create_alert(
    body: AlertCreate,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Alert:
    request.state.current_user = current_user
    now = datetime.now(timezone.utc)
    doc = {
        **body.model_dump(),
        "actif": True,
        "derniere_declenchee": None,
        "historique": [],
        "cree_le": now,
        "cree_par": str(current_user.id),
    }
    result = await db.alerts.insert_one(doc)
    doc["_id"] = result.inserted_id
    return Alert(**doc)


@router.get("/{alert_id}", dependencies=[Depends(require_viewer)])
async def get_alert(alert_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> Alert:
    return Alert(**await _get_alert_or_404(db, alert_id))


@router.patch("/{alert_id}", dependencies=[Depends(require_operator)])
async def update_alert(
    alert_id: str,
    body: AlertUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Alert:
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    await db.alerts.update_one({"_id": ObjectId(alert_id)}, {"$set": changes})
    return Alert(**await _get_alert_or_404(db, alert_id))


@router.delete("/{alert_id}")
async def delete_alert(
    alert_id: str,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    request.state.current_user = current_user
    await _get_alert_or_404(db, alert_id)
    await db.alerts.delete_one({"_id": ObjectId(alert_id)})
    return {"message": f"Alert {alert_id} deleted"}


@router.post("/{alert_id}/toggle", dependencies=[Depends(require_operator)])
async def toggle_alert(
    alert_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Alert:
    """Active ou désactive une alerte."""
    doc = await _get_alert_or_404(db, alert_id)
    new_state = not doc.get("actif", True)
    await db.alerts.update_one({"_id": ObjectId(alert_id)}, {"$set": {"actif": new_state}})
    return Alert(**await _get_alert_or_404(db, alert_id))
