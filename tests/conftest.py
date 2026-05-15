"""Fixtures pytest partagées pour les tests du backend Orbis."""
import asyncio
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


# ---------------------------------------------------------------------------
# Fixture : base de données MongoDB mockée
# ---------------------------------------------------------------------------

def _make_mock_db():
    """Crée un mock AsyncIOMotorDatabase avec des collections configurées."""
    db = MagicMock()

    async def mock_insert_one(doc):
        result = MagicMock()
        result.inserted_id = MagicMock()
        result.inserted_id.__str__ = lambda self: str(uuid.uuid4())
        return result

    async def mock_find_one(query=None, *args, **kwargs):
        return None

    async def mock_update_one(*args, **kwargs):
        result = MagicMock()
        result.modified_count = 1
        return result

    async def mock_count_documents(query=None):
        return 0

    async def mock_command(cmd):
        return {"ok": 1}

    async def _aiter_empty(*a, **kw):
        return
        yield  # noqa: unreachable — makes this an async generator

    for collection_name in [
        "devices", "logs", "commands", "users", "groups",
        "alerts", "actions", "instructions", "agent_versions",
        "audit_logs", "discovery",
    ]:
        col = MagicMock()
        col.insert_one = AsyncMock(side_effect=mock_insert_one)
        col.insert_many = AsyncMock(
            return_value=MagicMock(inserted_ids=[MagicMock()])
        )
        col.find_one = AsyncMock(side_effect=mock_find_one)
        col.update_one = AsyncMock(side_effect=mock_update_one)
        col.update_many = AsyncMock(side_effect=mock_update_one)
        col.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
        col.count_documents = AsyncMock(side_effect=mock_count_documents)
        col.create_index = AsyncMock(return_value="index_name")

        # Curseur async vide : __aiter__ doit être une callable retournant un async-generator
        cursor = MagicMock()
        cursor.__aiter__ = lambda *_: _aiter_empty()
        cursor.skip = MagicMock(return_value=cursor)
        cursor.limit = MagicMock(return_value=cursor)
        cursor.sort = MagicMock(return_value=cursor)
        col.find = MagicMock(return_value=cursor)

        setattr(db, collection_name, col)

    db.command = AsyncMock(side_effect=mock_command)
    return db


@pytest.fixture
def mock_db():
    return _make_mock_db()


# ---------------------------------------------------------------------------
# Fixture : application FastAPI de test
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def app_client(mock_db) -> AsyncGenerator[AsyncClient, None]:
    """
    Client HTTP async avec :
    - get_db remplacé via dependency_overrides (seule méthode fiable avec FastAPI)
    - MQTT client mocké pour éviter les logs de retry
    """
    from app.main import app
    from app.database import get_db

    # ✅ Méthode correcte pour mocker les dépendances FastAPI
    app.dependency_overrides[get_db] = lambda: mock_db

    with (
        patch("app.mqtt.client.run_mqtt_client", new_callable=AsyncMock),
        patch("app.mqtt.client.stop_mqtt_client", new_callable=AsyncMock),
        patch("app.services.auth_service.ensure_admin_exists", new_callable=AsyncMock),
        patch("app.database.connect_to_mongo", new_callable=AsyncMock),
        patch("app.database.close_mongo", new_callable=AsyncMock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client

    # Nettoyage indispensable — les overrides persistent sur l'objet app
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Fixtures : utilisateurs et tokens de test
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_token() -> str:
    """Génère un access token JWT valide pour l'admin de test."""
    from app.models.user import User, UserRole
    from app.services.auth_service import create_access_token, hash_password

    user = User(
        _id="507f1f77bcf86cd799439011",
        email="admin@orbis.local",
        hashed_password=hash_password("admin_secret"),
        full_name="Administrator",
        role=UserRole.admin,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    return create_access_token(user)


@pytest.fixture
def viewer_token() -> str:
    """Génère un access token JWT valide pour un viewer de test."""
    from app.models.user import User, UserRole
    from app.services.auth_service import create_access_token, hash_password

    user = User(
        _id="507f1f77bcf86cd799439012",
        email="viewer@orbis.local",
        hashed_password=hash_password("viewer_secret"),
        full_name="Viewer",
        role=UserRole.viewer,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    return create_access_token(user)


# ---------------------------------------------------------------------------
# Fixture : mock get_user_by_id (auth middleware) — partagée par les tests HTTP
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_admin_user():
    """Retourne un objet User admin pour les mocks d'auth."""
    from app.models.user import User, UserRole
    from app.services.auth_service import hash_password

    return User(
        _id="507f1f77bcf86cd799439011",
        email="admin@orbis.local",
        hashed_password=hash_password("admin_secret"),
        full_name="Administrator",
        role=UserRole.admin,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixtures : données de test réutilisables
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_device_id() -> str:
    return "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


@pytest.fixture
def sample_command_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def sample_register_payload(sample_device_id: str) -> dict:
    """Payload d'enregistrement conforme à Session 0, Section 2."""
    return {
        "device_id": sample_device_id,
        "hostname": "test-server-01",
        "plateforme": "linux",
        "os_version": "Ubuntu 22.04.3 LTS",
        "architecture": "x86_64",
        "version_agent": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
