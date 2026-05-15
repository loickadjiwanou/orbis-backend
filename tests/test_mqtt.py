"""Tests des handlers MQTT (Session 0, Sections 2, 3, 4, 6, 8)."""
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId


def _make_full_mock_db(device_id: str = None, existing_device: dict = None):
    """Mock DB avec comportements configurables."""
    db = MagicMock()

    db.devices.find_one = AsyncMock(return_value=existing_device)
    db.devices.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))
    db.devices.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    db.devices.update_many = AsyncMock(return_value=MagicMock(modified_count=0))

    db.logs.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=[ObjectId()]))
    db.logs.create_index = AsyncMock(return_value="index")

    db.commands.find_one = AsyncMock(return_value=None)
    db.commands.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

    db.alerts.find = MagicMock(return_value=_empty_async_cursor())
    db.groups.find = MagicMock(return_value=_empty_async_cursor())

    db.discovery.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))

    return db


def _empty_async_cursor():
    """Curseur async vide pour les find()."""
    async def _aiter(*_):
        return
        yield  # noqa: unreachable

    cursor = MagicMock()
    cursor.__aiter__ = lambda *_: _aiter()
    cursor.find = MagicMock(return_value=cursor)
    return cursor


def _full_device_doc(device_id: str, revoked: bool = False) -> dict:
    """Document Device complet avec tous les champs requis par le modèle."""
    return {
        "_id": ObjectId(),
        "device_id": device_id,
        "nom": device_id,
        "hostname": "test-host",
        "plateforme": "linux",
        "os_version": "Ubuntu 22.04",
        "architecture": "x86_64",
        "statut": "revoked" if revoked else "online",
        "version_agent": "1.0.0",
        "derniere_connexion": datetime.now(timezone.utc),
        "config_logs": {"interval_sec": 60, "levels": ["INFO"], "sources": []},
        "groupe_ids": [],
        "token_hash": "hash",
        "revoked": revoked,
        "created_at": datetime.now(timezone.utc),
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Test : handle_register — nouveau device (Session 0, Section 2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_register_new_device():
    """
    handle_register crée un nouveau device, appelle EMQX et publie register_ack.
    Vérifie le format de réponse Session 0, Section 2.
    """
    device_id = str(uuid.uuid4())
    payload = {
        "device_id": device_id,
        "hostname": "test-server-01",
        "plateforme": "linux",
        "os_version": "Ubuntu 22.04.3 LTS",
        "architecture": "x86_64",
        "version_agent": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    mock_db = _make_full_mock_db(device_id=device_id, existing_device=None)

    with (
        # ✅ Patcher où get_db EST utilisé, pas où il est défini
        patch("app.mqtt.handlers.get_db", return_value=mock_db),
        patch("app.services.emqx_service.emqx_service.create_mqtt_user", new_callable=AsyncMock, return_value=True),
        patch("app.mqtt.client.publish", new_callable=AsyncMock) as mock_publish,
        patch("app.websocket.manager.ws_manager.broadcast", new_callable=AsyncMock),
    ):
        from app.mqtt.handlers import handle_register
        await handle_register(payload)

    # Vérifie que insert_one a été appelé pour créer le device
    mock_db.devices.insert_one.assert_called_once()
    inserted_doc = mock_db.devices.insert_one.call_args[0][0]
    assert inserted_doc["device_id"] == device_id
    assert inserted_doc["hostname"] == "test-server-01"
    assert inserted_doc["plateforme"] == "linux"
    assert not inserted_doc["revoked"]

    # Vérifie la publication du register_ack (Session 0, Section 2, Étape 4)
    mock_publish.assert_called_once()
    topic_called = mock_publish.call_args[0][0]
    ack_payload_str = mock_publish.call_args[0][1]
    ack_payload = json.loads(ack_payload_str)

    assert topic_called == f"devices/{device_id}/register_ack"
    assert ack_payload["status"] == "ok"
    assert ack_payload["device_id"] == device_id
    assert ack_payload["token"] is not None
    assert ack_payload["message"] == "Device registered successfully"


# ---------------------------------------------------------------------------
# Test : handle_register — device révoqué
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_register_revoked_device():
    """handle_register retourne une erreur si le device est révoqué."""
    device_id = str(uuid.uuid4())
    payload = {
        "device_id": device_id,
        "hostname": "test",
        "plateforme": "linux",
        "os_version": "Ubuntu 22.04",
        "architecture": "x86_64",
        "version_agent": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    # ✅ Document complet avec tous les champs requis par le modèle Device
    existing_device = _full_device_doc(device_id, revoked=True)
    mock_db = _make_full_mock_db(device_id=device_id, existing_device=existing_device)

    with (
        patch("app.mqtt.handlers.get_db", return_value=mock_db),
        patch("app.mqtt.client.publish", new_callable=AsyncMock) as mock_publish,
    ):
        from app.mqtt.handlers import handle_register
        await handle_register(payload)

    ack_payload = json.loads(mock_publish.call_args[0][1])
    assert ack_payload["status"] == "error"
    assert ack_payload["token"] is None
    assert "revoked" in ack_payload["message"].lower()


# ---------------------------------------------------------------------------
# Test : handle_status met à jour le device (Session 0, Section 3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_status_updates_device():
    """handle_status doit mettre à jour le statut et notifier le dashboard."""
    device_id = str(uuid.uuid4())
    status_payload = {
        "device_id": device_id,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "online",
        "version": "1.0.0",
        "uptime_sec": 3600,
        "cpu_percent": 12.5,
        "ram_percent": 45.2,
        "disk_percent": 67.0,
    }

    mock_db = _make_full_mock_db(device_id=device_id)

    with (
        patch("app.mqtt.handlers.get_db", return_value=mock_db),
        patch("app.websocket.manager.ws_manager.broadcast", new_callable=AsyncMock) as mock_ws,
    ):
        from app.mqtt.handlers import handle_status
        await handle_status(device_id, status_payload)

    # Vérification que update_one a été appelé avec le bon statut
    mock_db.devices.update_one.assert_called_once()

    # Vérification notification WebSocket (event "device_status")
    mock_ws.assert_called_once()
    event = mock_ws.call_args[0][0]
    ws_data = mock_ws.call_args[0][1]
    assert event == "device_status"
    assert ws_data["device_id"] == device_id
    assert ws_data["statut"] == "online"
    assert ws_data["cpu_percent"] == 12.5


# ---------------------------------------------------------------------------
# Test : handle_logs persiste les logs et évalue les alertes (Session 0, Section 4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_log_triggers_alert():
    """handle_logs doit persister les logs et évaluer les alertes actives."""
    device_id = str(uuid.uuid4())
    log_batch = [
        {
            "device_id": device_id,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": "ERROR",
            "source": "disk_plugin",
            "message": "Disk usage critical: 97%",
            "metadata": {"percent": 97.0},
        }
    ]

    inserted_id = ObjectId()
    mock_db = _make_full_mock_db(device_id=device_id)
    mock_db.logs.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=[inserted_id]))

    with (
        patch("app.mqtt.handlers.get_db", return_value=mock_db),
        patch("app.websocket.manager.ws_manager.broadcast", new_callable=AsyncMock) as mock_ws,
        patch("app.services.alert_service.evaluate_alerts_for_log", new_callable=AsyncMock) as mock_alerts,
    ):
        from app.mqtt.handlers import handle_logs
        await handle_logs(device_id, log_batch)

    # Vérification persistance (insert_many appelé)
    mock_db.logs.insert_many.assert_called_once()
    inserted_docs = mock_db.logs.insert_many.call_args[0][0]
    assert len(inserted_docs) == 1
    assert inserted_docs[0]["level"] == "ERROR"
    assert inserted_docs[0]["source"] == "disk_plugin"

    # Vérification notification WebSocket (event "new_log")
    ws_calls = [call[0][0] for call in mock_ws.call_args_list]
    assert "new_log" in ws_calls

    # Vérification évaluation des alertes
    mock_alerts.assert_called_once()
