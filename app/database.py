import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING
from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def connect_to_mongo() -> None:
    """Établit la connexion à MongoDB et crée les index nécessaires."""
    global _client, _db
    logger.info("Connexion à MongoDB : %s", settings.MONGODB_URL)
    _client = AsyncIOMotorClient(settings.MONGODB_URL)
    _db = _client[settings.MONGODB_DB]
    await _setup_indexes()
    logger.info("MongoDB connecté — base : %s", settings.MONGODB_DB)


async def close_mongo() -> None:
    """Ferme proprement la connexion à MongoDB."""
    global _client
    if _client:
        _client.close()
        logger.info("MongoDB déconnecté.")


async def _setup_indexes() -> None:
    """Crée tous les index MongoDB nécessaires à la performance et à la rétention."""
    db = get_db()

    # Logs : TTL pour la rétention + index de recherche
    await db.logs.create_index(
        "received_at",
        expireAfterSeconds=settings.LOG_RETENTION_DAYS * 86400,
        name="logs_ttl",
    )
    await db.logs.create_index(
        [("device_id", ASCENDING), ("timestamp", DESCENDING)],
        name="logs_device_time",
    )
    await db.logs.create_index([("level", ASCENDING)], name="logs_level")
    await db.logs.create_index([("source", ASCENDING)], name="logs_source")

    # Devices : unicité du device_id
    await db.devices.create_index(
        "device_id", unique=True, name="devices_device_id_unique"
    )
    await db.devices.create_index([("statut", ASCENDING)], name="devices_statut")
    await db.devices.create_index(
        [("plateforme", ASCENDING)], name="devices_plateforme"
    )

    # Commands : recherche par device et statut
    await db.commands.create_index(
        [("device_id", ASCENDING), ("cree_le", DESCENDING)],
        name="commands_device_time",
    )
    await db.commands.create_index([("statut", ASCENDING)], name="commands_statut")

    # Audit : recherche par opérateur et date
    await db.audit_logs.create_index(
        [("timestamp", DESCENDING)], name="audit_timestamp"
    )
    await db.audit_logs.create_index(
        [("operateur_id", ASCENDING)], name="audit_operateur"
    )
    await db.audit_logs.create_index(
        [("device_id", ASCENDING)], name="audit_device"
    )

    # Users : unicité email
    await db.users.create_index("email", unique=True, name="users_email_unique")

    # Alerts : scope_id pour la recherche rapide
    await db.alerts.create_index([("scope_id", ASCENDING)], name="alerts_scope")

    # Discovery : par device
    await db.discovery.create_index(
        [("device_id", ASCENDING), ("timestamp", DESCENDING)],
        name="discovery_device_time",
    )

    logger.info("Index MongoDB créés.")


def get_db() -> AsyncIOMotorDatabase:
    """Retourne l'instance de la base de données (à utiliser via Depends)."""
    if _db is None:
        raise RuntimeError("La connexion MongoDB n'est pas initialisée.")
    return _db
