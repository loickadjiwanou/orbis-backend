"""
Handlers MQTT — un handler par topic.
Tous les formats JSON respectent SESSION 0 (contrat des interfaces).
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from passlib.context import CryptContext

from app.database import get_db
from app.models.command import CommandResult
from app.models.device import DeviceStatus
from app.models.log import LogIncoming
from app.services import alert_service, device_service, command_service
from app.services.emqx_service import emqx_service
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Section 2 — Onboarding : devices/register
# ---------------------------------------------------------------------------

async def handle_register(data: Dict[str, Any]) -> None:
    """
    Traite un message d'enregistrement d'agent.
    Topic : devices/register (Session 0, Section 2)
    Répond sur : devices/{device_id}/register_ack
    """
    # Import ici pour éviter les imports circulaires
    from app.mqtt.client import publish

    device_id = data.get("device_id", "")
    if not device_id:
        logger.warning("handle_register : device_id manquant")
        return

    required = ["hostname", "plateforme", "os_version", "architecture", "version_agent", "timestamp"]
    for field in required:
        if field not in data:
            logger.warning("handle_register : champ manquant '%s' pour device=%s", field, device_id)
            ack_topic = f"devices/{device_id}/register_ack"
            ack_payload = json.dumps({
                "status": "error",
                "device_id": device_id,
                "token": None,
                "message": f"Registration failed: missing field '{field}'",
            })
            await publish(ack_topic, ack_payload, qos=1, retain=False)
            return

    db = get_db()

    # Vérification device existant
    existing = await device_service.get_device_by_device_id(db, device_id)
    token: str

    if existing is not None:
        if existing.revoked:
            ack_topic = f"devices/{device_id}/register_ack"
            ack_payload = json.dumps({
                "status": "error",
                "device_id": device_id,
                "token": None,
                "message": "Registration failed: device revoked",
            })
            await publish(ack_topic, ack_payload, qos=1, retain=False)
            logger.warning("handle_register : device révoqué device=%s", device_id)
            return
        # Re-onboarding : on génère un nouveau token
        token = str(uuid.uuid4())
        token_hash = _pwd_context.hash(token)
        await db.devices.update_one(
            {"device_id": device_id},
            {"$set": {
                "token_hash": token_hash,
                "hostname": data["hostname"],
                "plateforme": data["plateforme"],
                "os_version": data["os_version"],
                "architecture": data["architecture"],
                "version_agent": data["version_agent"],
                "statut": DeviceStatus.online,
                "derniere_connexion": datetime.now(timezone.utc),
            }},
        )
        logger.info("Re-onboarding device=%s", device_id)
    else:
        # Nouveau device
        token = str(uuid.uuid4())
        token_hash = _pwd_context.hash(token)
        now = datetime.now(timezone.utc)
        doc = {
            "device_id": device_id,
            "nom": data.get("hostname", device_id),
            "hostname": data["hostname"],
            "plateforme": data["plateforme"],
            "os_version": data["os_version"],
            "architecture": data["architecture"],
            "version_agent": data["version_agent"],
            "statut": DeviceStatus.online,
            "derniere_connexion": now,
            "config_logs": {"interval_sec": 60, "levels": ["INFO", "WARNING", "ERROR", "CRITICAL"], "sources": []},
            "groupe_ids": [],
            "token_hash": token_hash,
            "revoked": False,
            "created_at": now,
            "metadata": {},
        }
        await db.devices.insert_one(doc)
        logger.info("Nouveau device enregistré : device=%s plateforme=%s", device_id, data["plateforme"])

    # Créer le compte MQTT dans EMQX
    ok = await emqx_service.create_mqtt_user(device_id, token)
    if not ok:
        logger.error("Echec création compte MQTT pour device=%s", device_id)

    # Publier le register_ack (Session 0, Section 2, Étape 4)
    ack_topic = f"devices/{device_id}/register_ack"
    ack_payload = json.dumps({
        "status": "ok",
        "device_id": device_id,
        "token": token,
        "message": "Device registered successfully",
    })
    await publish(ack_topic, ack_payload, qos=1, retain=False)

    # Notifier le dashboard via WebSocket (Session 0, Section 9)
    logger.info(
        "Broadcasting device_registered to %d WS client(s) for device=%s",
        ws_manager.connected_count,
        device_id,
    )
    await ws_manager.broadcast("device_registered", {
        "device_id": device_id,
        "nom": data.get("hostname", device_id),
        "plateforme": data["plateforme"],
        "version": data["version_agent"],
        "statut": "online",
    })


# ---------------------------------------------------------------------------
# Section 4 — Logs : devices/{device_id}/logs
# ---------------------------------------------------------------------------

async def handle_logs(device_id: str, data: Any) -> None:
    """
    Traite un batch de logs reçu d'un agent.
    data peut être une liste (batch) ou un objet unique.
    Topic : devices/{device_id}/logs (Session 0, Section 4)
    """
    db = get_db()

    # Normaliser en liste
    entries: List[Dict[str, Any]] = data if isinstance(data, list) else [data]
    if len(entries) > 100:
        logger.warning("handle_logs : batch trop grand (%d), troncature à 100", len(entries))
        entries = entries[:100]

    now = datetime.now(timezone.utc)
    docs_to_insert = []

    for entry in entries:
        try:
            # Forcer le device_id du topic (sécurité)
            entry["device_id"] = device_id
            log_in = LogIncoming(**entry)
        except Exception as exc:
            logger.warning("handle_logs : log invalide device=%s : %s", device_id, exc)
            continue

        doc = {
            "device_id": log_in.device_id,
            "timestamp": log_in.timestamp,
            "level": log_in.level,
            "source": log_in.source,
            "message": log_in.message,
            "metadata": log_in.metadata,
            "received_at": now,
        }
        docs_to_insert.append(doc)

    if not docs_to_insert:
        return

    result = await db.logs.insert_many(docs_to_insert)

    # Mise à jour dernière connexion du device
    await device_service.update_device_status(db, device_id, DeviceStatus.online)

    # Notifier le dashboard pour chaque log + évaluer les alertes
    for i, doc in enumerate(docs_to_insert):
        doc["_id"] = result.inserted_ids[i]
        from app.models.log import Log
        log_obj = Log(**doc)

        await ws_manager.broadcast("new_log", {
            "id": str(doc["_id"]),
            "device_id": log_obj.device_id,
            "timestamp": log_obj.timestamp.isoformat().replace("+00:00", "Z"),
            "level": log_obj.level,
            "source": log_obj.source,
            "message": log_obj.message,
            "metadata": log_obj.metadata,
        })

        await alert_service.evaluate_alerts_for_log(
            db,
            log_obj,
            publish_command_fn=_publish_action_as_command,
            ws_notify_fn=ws_manager.broadcast,
        )


# ---------------------------------------------------------------------------
# Section 3 — Heartbeat : devices/{device_id}/status
# ---------------------------------------------------------------------------

async def handle_status(device_id: str, data: Dict[str, Any]) -> None:
    """
    Traite un message de statut/heartbeat d'un agent.
    Topic : devices/{device_id}/status (Session 0, Section 3)
    """
    db = get_db()
    raw_status = data.get("status", "online")
    statut = DeviceStatus.online if raw_status == "online" else DeviceStatus.offline

    extra = {}
    for field in ("version", "cpu_percent", "ram_percent", "disk_percent",
                  "battery_level", "battery_charging", "network_type", "storage_percent",
                  "uptime_sec"):
        if field in data:
            extra[field] = data[field]
    if "version" in extra:
        extra["version_agent"] = extra.pop("version")

    await device_service.update_device_status(db, device_id, statut, extra)

    # Notifier le dashboard (Session 0, Section 9 : event "device_status")
    await ws_manager.broadcast("device_status", {
        "device_id": device_id,
        "statut": statut,
        "version": data.get("version", ""),
        "derniere_connexion": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cpu_percent": data.get("cpu_percent"),
        "ram_percent": data.get("ram_percent"),
    })

    # Évaluer les alertes metadata_threshold sur les métriques du heartbeat
    if statut == DeviceStatus.online:
        heartbeat_metrics: dict = {}
        for field in ("cpu_percent", "ram_percent", "disk_percent", "storage_percent",
                      "battery_level", "battery_charging", "network_type", "uptime_sec"):
            if field in data:
                heartbeat_metrics[field] = data[field]
        if heartbeat_metrics:
            await alert_service.evaluate_alerts_for_heartbeat(
                db, device_id, heartbeat_metrics, ws_manager.broadcast
            )


# ---------------------------------------------------------------------------
# Section 6 — Résultats : devices/{device_id}/results
# ---------------------------------------------------------------------------

async def handle_results(device_id: str, data: Dict[str, Any]) -> None:
    """
    Traite un résultat de commande envoyé par un agent.
    Topic : devices/{device_id}/results (Session 0, Section 6)
    Cycle : acknowledged → executing → success/failed
    """
    db = get_db()

    try:
        result = CommandResult(**data)
    except Exception as exc:
        logger.warning("handle_results : payload invalide device=%s : %s", device_id, exc)
        return

    command = await command_service.apply_command_result(db, result)
    if command is None:
        logger.warning("handle_results : command_id=%s introuvable", result.command_id)
        return

    # Notifier le dashboard (Session 0, Section 9 : event "command_update")
    await ws_manager.broadcast("command_update", {
        "command_id": result.command_id,
        "device_id": device_id,
        "statut": result.statut,
        "output": result.output,
        "error": result.error,
        "exit_code": result.exit_code,
        "termine_le": command.termine_le.isoformat().replace("+00:00", "Z") if command.termine_le else None,
    })


# ---------------------------------------------------------------------------
# Section 8 — Découverte réseau : devices/{device_id}/discovery
# ---------------------------------------------------------------------------

async def handle_discovery(device_id: str, data: Dict[str, Any]) -> None:
    """
    Traite un résultat de scan réseau.
    Topic : devices/{device_id}/discovery (Session 0, Section 8)
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    doc = {
        "device_id": device_id,
        "timestamp": data.get("timestamp", now.isoformat()),
        "network": data.get("network", ""),
        "neighbors": data.get("neighbors", []),
        "received_at": now,
    }
    await db.discovery.insert_one(doc)

    neighbors_count = len(data.get("neighbors", []))
    logger.info("Discovery device=%s réseau=%s voisins=%d", device_id, data.get("network"), neighbors_count)

    # Notifier le dashboard (Session 0, Section 9 : event "discovery_update")
    await ws_manager.broadcast("discovery_update", {
        "device_id": device_id,
        "neighbors_count": neighbors_count,
        "network": data.get("network", ""),
    })


# ---------------------------------------------------------------------------
# Section X — Agent internal logs : devices/{device_id}/agent_logs
# ---------------------------------------------------------------------------

async def handle_agent_logs(device_id: str, data: dict) -> None:
    """
    Stores an agent internal log record.
    Topic: devices/{device_id}/agent_logs
    Payload: { device_id, timestamp, level, source, message }
    """
    from datetime import datetime, timezone
    db = get_db()
    now = datetime.now(timezone.utc)
    try:
        ts_raw = data.get("timestamp", now.isoformat())
        if isinstance(ts_raw, str):
            timestamp = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        else:
            timestamp = now

        level = str(data.get("level", "INFO")).upper()
        if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            level = "INFO"

        doc = {
            "device_id": device_id,
            "timestamp": timestamp,
            "level": level,
            "source": str(data.get("source", "agent"))[:100],
            "message": str(data.get("message", ""))[:10000],
            "received_at": now,
        }
        await db.agent_logs.insert_one(doc)
    except Exception as exc:
        logger.warning("handle_agent_logs: invalid payload device=%s: %s", device_id, exc)


# ---------------------------------------------------------------------------
# Section 7 — Progression mise à jour : devices/{device_id}/update_progress
# ---------------------------------------------------------------------------

async def handle_update_progress(device_id: str, data: Dict[str, Any]) -> None:
    """
    Relaie un événement de progression de mise à jour de l'agent vers le dashboard.
    Topic : devices/{device_id}/update_progress
    Payload : { command_id, statut, step, output?, error? }
    """
    await ws_manager.broadcast("agent_update_progress", {
        "command_id": data.get("command_id", ""),
        "device_id": device_id,
        "statut": data.get("statut", ""),
        "step": data.get("step", ""),
        "output": data.get("output"),
        "error": data.get("error"),
    })


# ---------------------------------------------------------------------------
# Helper interne
# ---------------------------------------------------------------------------

async def _publish_action_as_command(device_id: str, action_doc: Dict[str, Any]) -> None:
    """Publie une action en tant que commande shell sur un device."""
    from app.mqtt.client import publish
    cmd_id = str(uuid.uuid4())
    payload = {
        "command_id": cmd_id,
        "device_id": device_id,
        "type": "shell",
        "payload": {"command": action_doc.get("script_template", ""), "shell": "/bin/bash"},
        "timeout_sec": 30,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    topic = f"devices/{device_id}/commands"
    await publish(topic, json.dumps(payload), qos=1, retain=False)
