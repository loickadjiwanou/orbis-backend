"""Endpoints de consultation des logs d'audit."""
import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.middleware.auth import require_viewer
from app.services.audit_service import list_audit_logs

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", dependencies=[Depends(require_viewer)])
async def get_audit_logs(
    operateur_id: Optional[str] = None,
    action_type: Optional[str] = None,
    device_id: Optional[str] = None,
    resultat: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """Liste paginée des logs d'audit avec filtres."""
    sd = datetime.fromisoformat(start_date) if start_date else None
    ed = datetime.fromisoformat(end_date) if end_date else None
    return await list_audit_logs(
        db,
        operateur_id=operateur_id,
        action_type=action_type,
        device_id=device_id,
        resultat=resultat,
        start_date=sd,
        end_date=ed,
        skip=skip,
        limit=limit,
    )


@router.get("/export", dependencies=[Depends(require_viewer)])
async def export_audit_csv(
    operateur_id: Optional[str] = None,
    action_type: Optional[str] = None,
    device_id: Optional[str] = None,
    resultat: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> StreamingResponse:
    """Exporte les logs d'audit au format CSV."""
    sd = datetime.fromisoformat(start_date) if start_date else None
    ed = datetime.fromisoformat(end_date) if end_date else None
    result = await list_audit_logs(
        db,
        operateur_id=operateur_id,
        action_type=action_type,
        device_id=device_id,
        resultat=resultat,
        start_date=sd,
        end_date=ed,
        skip=0,
        limit=10000,
    )

    output = io.StringIO()
    fieldnames = [
        "id", "operateur_id", "operateur_email", "action_type",
        "device_id", "timestamp", "resultat", "ip_address", "user_agent",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for log in result["items"]:
        row = log.model_dump(by_alias=True)
        row["id"] = str(row.get("_id", ""))
        writer.writerow(row)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_export.csv"},
    )
