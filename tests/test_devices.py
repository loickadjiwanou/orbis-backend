"""Tests des endpoints REST pour les devices."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId


# ---------------------------------------------------------------------------
# Test : GET /devices (liste)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_devices_list(app_client, admin_token, mock_db, mock_admin_user):
    """GET /devices retourne une liste paginée vide (mock DB)."""
    with patch("app.middleware.auth.get_user_by_id", new_callable=AsyncMock) as mock_user:
        mock_user.return_value = mock_admin_user
        resp = await app_client.get(
            "/devices",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] == 0


# ---------------------------------------------------------------------------
# Test : GET /devices/{device_id} — not found
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_device_not_found(app_client, admin_token, mock_db, mock_admin_user, sample_device_id):
    """GET /devices/{id} retourne 404 si le device n'existe pas."""
    with patch("app.middleware.auth.get_user_by_id", new_callable=AsyncMock) as mock_user:
        mock_user.return_value = mock_admin_user
        resp = await app_client.get(
            f"/devices/{sample_device_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Device not found"


# ---------------------------------------------------------------------------
# Test : POST /devices/{id}/command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_command_to_device(app_client, admin_token, mock_db, mock_admin_user, sample_device_id):
    """POST /devices/{id}/command crée une commande et la publie en MQTT."""
    from app.models.device import DeviceStatus

    device_doc = {
        "_id": ObjectId(),
        "device_id": sample_device_id,
        "nom": "Test Server",
        "hostname": "test-server",
        "plateforme": "linux",
        "os_version": "Ubuntu 22.04",
        "architecture": "x86_64",
        "statut": DeviceStatus.online,
        "version_agent": "1.0.0",
        "derniere_connexion": datetime.now(timezone.utc),
        "config_logs": {"interval_sec": 60, "levels": ["INFO"], "sources": []},
        "groupe_ids": [],
        "token_hash": "hash",
        "revoked": False,
        "created_at": datetime.now(timezone.utc),
        "metadata": {},
    }
    cmd_doc = {
        "_id": ObjectId(),
        "command_id": str(uuid.uuid4()),
        "device_id": sample_device_id,
        "type": "shell",
        "payload": {"command": "df -h"},
        "timeout_sec": 30,
        "statut": "sent",
        "resultat": None,
        "error_message": None,
        "exit_code": None,
        "cree_le": datetime.now(timezone.utc),
        "envoye_le": datetime.now(timezone.utc),
        "acquitte_le": None,
        "execute_le": None,
        "termine_le": None,
        "cree_par": "507f1f77bcf86cd799439011",
    }

    mock_db.devices.find_one = AsyncMock(return_value=device_doc)
    mock_db.commands.find_one = AsyncMock(return_value=cmd_doc)
    mock_db.commands.insert_one = AsyncMock(return_value=MagicMock(inserted_id=cmd_doc["_id"]))
    mock_db.commands.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

    with (
        patch("app.middleware.auth.get_user_by_id", new_callable=AsyncMock) as mock_user,
        patch("app.mqtt.client.publish", new_callable=AsyncMock),
    ):
        mock_user.return_value = mock_admin_user
        resp = await app_client.post(
            f"/devices/{sample_device_id}/command",
            json={"type": "shell", "payload": {"command": "df -h"}, "timeout_sec": 30},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["device_id"] == sample_device_id
    assert data["type"] == "shell"


# ---------------------------------------------------------------------------
# Test : DELETE /devices/{id} — révocation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revoke_device(app_client, admin_token, mock_db, mock_admin_user, sample_device_id):
    """DELETE /devices/{id} révoque le device et appelle EMQX."""
    from app.models.device import DeviceStatus

    device_doc = {
        "_id": ObjectId(),
        "device_id": sample_device_id,
        "nom": "Test",
        "hostname": "test",
        "plateforme": "linux",
        "os_version": "Ubuntu 22.04",
        "architecture": "x86_64",
        "statut": DeviceStatus.online,
        "version_agent": "1.0.0",
        "derniere_connexion": datetime.now(timezone.utc),
        "config_logs": {"interval_sec": 60, "levels": ["INFO"], "sources": []},
        "groupe_ids": [],
        "token_hash": "hash",
        "revoked": False,
        "created_at": datetime.now(timezone.utc),
        "metadata": {},
    }
    mock_db.devices.find_one = AsyncMock(return_value=device_doc)
    mock_db.devices.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

    with (
        patch("app.middleware.auth.get_user_by_id", new_callable=AsyncMock) as mock_user,
        patch("app.services.emqx_service.emqx_service.kick_client", new_callable=AsyncMock, return_value=True),
        patch("app.services.emqx_service.emqx_service.delete_mqtt_user", new_callable=AsyncMock, return_value=True),
    ):
        mock_user.return_value = mock_admin_user
        resp = await app_client.delete(
            f"/devices/{sample_device_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200, resp.text
    assert sample_device_id in resp.json()["message"]


# ---------------------------------------------------------------------------
# Test : GET /health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_check(app_client, mock_db):
    """GET /health retourne status ok."""
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
