"""Service d'évaluation et de déclenchement des alertes."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.alert import Alert, AlertConditionType
from app.models.log import Log
from app.services import email_service

logger = logging.getLogger(__name__)

# Verrou par alert_id pour éviter les doubles-envois d'email lors de
# heartbeats simultanés qui arrivent avant que email_last_sent_at soit
# persisté en base.
_email_locks: Dict[str, asyncio.Lock] = {}


def _get_email_lock(alert_id: str) -> asyncio.Lock:
    """Retourne (et crée si besoin) le Lock asyncio associé à un alert_id."""
    if alert_id not in _email_locks:
        _email_locks[alert_id] = asyncio.Lock()
    return _email_locks[alert_id]


def _evaluate_condition(alert: Alert, log: Log) -> bool:
    """
    Évalue si la condition d'une alerte est remplie pour un log donné.
    Types : log_level | metadata_threshold (inactivity géré en job séparé).
    """
    cond = alert.condition
    op = cond.operateur

    if cond.type == AlertConditionType.log_level:
        return _compare(log.level, op, cond.valeur)

    if cond.type == AlertConditionType.metadata_threshold:
        if not cond.metadata_key:
            return False
        actual = log.metadata.get(cond.metadata_key)
        if actual is None:
            return False
        return _compare(actual, op, cond.valeur)

    # inactivity : géré par le job périodique, jamais déclenché ici
    return False


def _compare(actual: Any, operateur: str, expected: Any) -> bool:
    """Applique un opérateur de comparaison entre deux valeurs."""
    try:
        if operateur == "eq":
            return actual == expected
        if operateur == "gt":
            return float(actual) > float(expected)
        if operateur == "lt":
            return float(actual) < float(expected)
        if operateur == "gte":
            return float(actual) >= float(expected)
        if operateur == "lte":
            return float(actual) <= float(expected)
        if operateur == "contains":
            return str(expected) in str(actual)
    except (TypeError, ValueError):
        pass
    return False


async def evaluate_alerts_for_log(
    db: AsyncIOMotorDatabase,
    log: Log,
    publish_command_fn,  # callable async (device_id, action) → None
    ws_notify_fn,        # callable async (event, data) → None
) -> None:
    """
    Évalue toutes les alertes actives pouvant se déclencher sur ce log.
    Appelé après chaque log persisté.
    """
    # Récupère les groupes contenant ce device
    group_ids = await _get_group_ids_for_device(db, log.device_id)

    # Toutes les alertes actives qui concernent ce device ou ses groupes
    scope_ids = [log.device_id] + group_ids
    cursor = db.alerts.find({"actif": True, "scope_id": {"$in": scope_ids}})

    async for doc in cursor:
        alert = Alert(**doc)
        if not _evaluate_condition(alert, log):
            continue

        logger.info("Alerte déclenchée : %s pour device=%s", alert.nom, log.device_id)
        now = datetime.now(timezone.utc)

        # Enregistrement dans l'historique
        history_entry = {
            "triggered_at": now,
            "device_id": log.device_id,
            "context": {
                "log_level": log.level,
                "log_source": log.source,
                "log_message": log.message[:500],
                "metadata": log.metadata,
            },
        }
        await db.alerts.update_one(
            {"_id": ObjectId(str(alert.id))},
            {
                "$set": {"derniere_declenchee": now},
                "$push": {"historique": history_entry},
            },
        )

        # Action automatique
        if alert.action_id:
            await _trigger_action(db, alert, log, publish_command_fn)

        # Webhook
        if alert.webhook_url:
            await _fire_webhook(alert, log.device_id, history_entry["context"])

        # Email (avec gestion du cooldown)
        if alert.email_recipients:
            await _maybe_send_alert_email(db, alert, log.device_id, history_entry["context"], now)

        # Notifier le dashboard
        await ws_notify_fn(
            "alert_triggered",
            {
                "alert_id": str(alert.id),
                "alert_nom": alert.nom,
                "device_id": log.device_id,
                "condition": f"{alert.condition.type}: {alert.condition.metadata_key or ''} {alert.condition.operateur} {alert.condition.valeur}",
                "log": {
                    "level": log.level,
                    "source": log.source,
                    "message": log.message,
                },
            },
        )


async def _get_group_ids_for_device(
    db: AsyncIOMotorDatabase, device_id: str
) -> list[str]:
    """Retourne les IDs des groupes contenant ce device."""
    cursor = db.groups.find({"device_ids": device_id}, {"_id": 1})
    return [str(doc["_id"]) async for doc in cursor]


async def _get_device_name(db: AsyncIOMotorDatabase, device_id: str) -> str:
    """Retourne le nom lisible du device (fallback: device_id)."""
    doc = await db.devices.find_one({"device_id": device_id}, {"nom": 1})
    if doc and doc.get("nom"):
        return str(doc["nom"])
    return device_id


async def _maybe_send_alert_email(
    db: AsyncIOMotorDatabase,
    alert: Alert,
    device_id: str,
    context: Dict[str, Any],
    now: datetime,
) -> None:
    """
    Envoie l'email d'alerte en respectant le cooldown configuré sur l'alerte.

    Logique :
      - Si email_cooldown_minutes == 0  → toujours envoyer (aucun délai)
      - Sinon, vérifier email_last_sent_at :
          • Jamais envoyé  → envoyer
          • Temps écoulé >= cooldown → envoyer
          • Temps écoulé <  cooldown → ignorer et loguer
    Après envoi réussi, mettre à jour email_last_sent_at dans MongoDB.

    Un verrou asyncio par alert_id garantit qu'un seul envoi concurrent
    est possible : si deux heartbeats arrivent simultanément, le second
    voit email_last_sent_at déjà mis à jour par le premier et est bloqué.
    """
    lock = _get_email_lock(str(alert.id))
    async with lock:
        # Re-lire email_last_sent_at depuis la DB sous le verrou pour avoir
        # la valeur la plus fraîche (le premier concurrent l'a peut-être mis à jour)
        fresh = await db.alerts.find_one(
            {"_id": ObjectId(str(alert.id))},
            {"email_last_sent_at": 1},
        )
        last_sent = fresh.get("email_last_sent_at") if fresh else None

        cooldown = alert.email_cooldown_minutes
        if cooldown > 0 and last_sent is not None:
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=timezone.utc)
            elapsed_minutes = (now - last_sent).total_seconds() / 60
            if elapsed_minutes < cooldown:
                remaining = cooldown - elapsed_minutes
                logger.debug(
                    "Email cooldown actif pour alerte '%s' : %.1f min restante(s) (cooldown: %d min)",
                    alert.nom, remaining, cooldown,
                )
                return

        device_name = await _get_device_name(db, device_id)
        await email_service.send_alert_email(
            alert=alert,
            device_id=device_id,
            device_name=device_name,
            context=context,
            recipients=alert.email_recipients,
        )

        # Mise à jour du timestamp du dernier envoi
        await db.alerts.update_one(
            {"_id": ObjectId(str(alert.id))},
            {"$set": {"email_last_sent_at": now}},
        )


async def evaluate_alerts_for_heartbeat(
    db: AsyncIOMotorDatabase,
    device_id: str,
    metrics: Dict[str, Any],
    ws_notify_fn,
) -> None:
    """
    Évalue les alertes metadata_threshold lors de la réception d'un heartbeat.
    metrics : dict des valeurs numériques du heartbeat (cpu_percent, ram_percent,
              disk_percent, battery_level, uptime_sec, …).

    Pour éviter de spammer l'historique à chaque heartbeat (toutes les ~30s),
    un cooldown de déclenchement de 60s est appliqué : si l'alerte a déjà été
    déclenchée dans la dernière minute, on ne recrée pas d'entrée d'historique
    (mais l'email reste soumis à son propre cooldown séparé).
    """
    TRIGGER_DEDUP_SEC = 60  # anti-spam historique

    group_ids = await _get_group_ids_for_device(db, device_id)
    scope_ids = [device_id] + group_ids

    cursor = db.alerts.find({
        "actif": True,
        "scope_id": {"$in": scope_ids},
        "condition.type": "metadata_threshold",
    })

    now = datetime.now(timezone.utc)

    async for doc in cursor:
        alert = Alert(**doc)
        cond = alert.condition

        if not cond.metadata_key:
            continue

        actual = metrics.get(cond.metadata_key)
        if actual is None:
            continue

        if not _compare(actual, cond.operateur, cond.valeur):
            continue

        # Deduplication : si déjà déclenché il y a moins de TRIGGER_DEDUP_SEC, skip l'historique
        if alert.derniere_declenchee is not None:
            last = alert.derniere_declenchee
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (now - last).total_seconds()
            if elapsed < TRIGGER_DEDUP_SEC:
                # Toujours tenter l'email (son propre cooldown gère le throttle)
                if alert.email_recipients:
                    await _maybe_send_alert_email(db, alert, device_id, {
                        "source": "heartbeat",
                        cond.metadata_key: actual,
                    }, now)
                continue

        logger.info(
            "Alerte heartbeat déclenchée : '%s' device=%s (%s=%s %s %s)",
            alert.nom, device_id, cond.metadata_key, actual, cond.operateur, cond.valeur,
        )

        context = {
            "source": "heartbeat",
            "metric_key": cond.metadata_key,
            "metric_value": actual,
            "all_metrics": {k: v for k, v in metrics.items()},
        }
        history_entry = {
            "triggered_at": now,
            "device_id": device_id,
            "context": context,
        }

        await db.alerts.update_one(
            {"_id": ObjectId(str(alert.id))},
            {
                "$set": {"derniere_declenchee": now},
                "$push": {"historique": history_entry},
            },
        )

        if alert.webhook_url:
            await _fire_webhook(alert, device_id, context)

        if alert.email_recipients:
            await _maybe_send_alert_email(db, alert, device_id, context, now)

        await ws_notify_fn(
            "alert_triggered",
            {
                "alert_id": str(alert.id),
                "alert_nom": alert.nom,
                "device_id": device_id,
                "condition": (
                    f"{cond.metadata_key} {cond.operateur} {cond.valeur}"
                    f" (heartbeat: {actual})"
                ),
            },
        )


async def _trigger_action(
    db: AsyncIOMotorDatabase,
    alert: Alert,
    log: Log,
    publish_command_fn,
) -> None:
    """Exécute l'action configurée sur l'alerte."""
    doc = await db.actions.find_one({"_id": ObjectId(alert.action_id)})
    if not doc:
        logger.warning("Action introuvable : %s", alert.action_id)
        return
    # Résolution des paramètres depuis le log metadata
    await publish_command_fn(log.device_id, doc)


async def _fire_webhook(alert: Alert, device_id: str, context: Dict[str, Any]) -> None:
    """Envoie un POST HTTP vers le webhook configuré sur l'alerte."""
    payload = {
        "alert_nom": alert.nom,
        "device_id": device_id,
        "context": context,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(str(alert.webhook_url), json=payload)
    except httpx.RequestError as exc:
        logger.warning("Webhook échec pour alerte %s : %s", alert.nom, exc)
