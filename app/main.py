"""Point d'entrée principal de l'application FastAPI Orbis."""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html
from fastapi.responses import HTMLResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import settings
from app.database import connect_to_mongo, close_mongo, get_db
from app.middleware.audit import AuditMiddleware
from app.mqtt import client as mqtt_client
from app.openapi_config import OPENAPI_TAGS, setup_openapi
from app.services.device_service import mark_inactive_devices_offline
from app.services import email_service
from app.websocket.manager import ws_manager
from app.middleware.auth import get_optional_ws_user
from app.routers import (
    auth,
    devices,
    groups,
    logs,
    commands,
    actions,
    instructions,
    alerts,
    agent_versions,
    audit,
    discovery,
    agent_logs,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Résultat du check SMTP au démarrage — partagé avec l'endpoint WebSocket
# pour notifier les clients qui se connectent après le test.
# ---------------------------------------------------------------------------

_smtp_notification: dict | None = None  # {title, message, type}

# ---------------------------------------------------------------------------
# Tâche de surveillance d'inactivité des devices
# ---------------------------------------------------------------------------

_inactivity_task: asyncio.Task | None = None


async def _inactivity_watcher() -> None:
    """
    Vérifie toutes les 60 secondes si des devices online n'ont pas envoyé
    de heartbeat depuis 5 minutes (Session 0, Section 14).
    Les passe à 'offline' et notifie le dashboard.
    """
    while True:
        try:
            await asyncio.sleep(60)
            db = get_db()
            count = await mark_inactive_devices_offline(db, timeout_sec=300)
            if count:
                await ws_manager.broadcast("device_status", {
                    "event": "inactivity_check",
                    "devices_marked_offline": count,
                })
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Erreur inactivity watcher : %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Lifespan FastAPI (démarrage / arrêt)
# ---------------------------------------------------------------------------

async def _smtp_startup_check() -> None:
    """
    Vérifie la configuration SMTP ~5 s après le démarrage, puis notifie
    tous les clients WebSocket déjà connectés et stocke le résultat pour
    les clients qui se connecteront après.
    """
    global _smtp_notification
    await asyncio.sleep(5)
    try:
        ok, detail = await email_service.check_smtp_config()
    except Exception as exc:
        ok, detail = False, f"Unexpected error during SMTP check: {exc}"

    if ok:
        logger.info("SMTP check passed: %s", detail)
        _smtp_notification = {
            "title": "Email Notifications Ready",
            "message": detail,
            "type": "success",
        }
    else:
        logger.warning("SMTP check failed: %s", detail)
        _smtp_notification = {
            "title": "Email Configuration Issue",
            "message": detail,
            "type": "error",
        }

    await ws_manager.broadcast("system_notification", _smtp_notification)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Gère le cycle de vie de l'application : connexions et tâches de fond."""
    global _inactivity_task

    logger.info("=== Orbis Backend v%s — démarrage ===", settings.APP_VERSION)

    # 0. Validation des secrets (bloque le démarrage si PRODUCTION=true et secrets par défaut)
    settings.check_production_secrets()

    # 1. MongoDB
    await connect_to_mongo()

    # 2. Client MQTT (tâche de fond)
    mqtt_task = asyncio.create_task(mqtt_client.run_mqtt_client(), name="mqtt-client")

    # 3. Surveillance d'inactivité
    _inactivity_task = asyncio.create_task(_inactivity_watcher(), name="inactivity-watcher")

    # 4. Vérification SMTP au démarrage (non bloquant)
    asyncio.create_task(_smtp_startup_check(), name="smtp-check")

    logger.info("Orbis Backend prêt.")
    yield

    # Arrêt propre
    logger.info("=== Orbis Backend — arrêt ===")
    await mqtt_client.stop_mqtt_client()
    mqtt_task.cancel()
    _inactivity_task.cancel()
    try:
        await asyncio.gather(mqtt_task, _inactivity_task, return_exceptions=True)
    except Exception:
        pass
    await close_mongo()
    logger.info("Orbis Backend arrêté.")


# ---------------------------------------------------------------------------
# Rate limiter (slowapi) — partagé avec les routers via app.state.limiter
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address, default_limits=[])

# ---------------------------------------------------------------------------
# Application FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Orbis Backend",
    description="API de supervision et contrôle de devices via MQTT/TLS",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,  # served manually below with a stable CDN version
    openapi_tags=OPENAPI_TAGS,
)

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Audit
app.add_middleware(AuditMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(groups.router)
app.include_router(logs.router)
app.include_router(commands.router)
app.include_router(actions.router)
app.include_router(instructions.router)
app.include_router(alerts.router)
app.include_router(agent_versions.router)
app.include_router(audit.router)
app.include_router(discovery.router)
app.include_router(agent_logs.router)

# Inject full OpenAPI descriptions and examples (Swagger UI + ReDoc)
setup_openapi(app)


@app.get("/redoc", include_in_schema=False)
async def redoc() -> HTMLResponse:
    """ReDoc UI served with a stable CDN bundle (redoc@next is 404 on jsdelivr)."""
    return get_redoc_html(
        openapi_url="/openapi.json",
        title="Orbis API — ReDoc",
        redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@2.1.3/bundles/redoc.standalone.js",
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    tags=["system"],
    summary="System health check",
    description=(
        "Returns the operational status of the backend, MongoDB connection, "
        "MQTT broker connection, application version, and the number of currently "
        "connected WebSocket clients.\n\n"
        "This endpoint does **not** require authentication. "
        "It is used by Docker HEALTHCHECK and load-balancer probes."
    ),
    response_description="System status object",
    responses={
        200: {
            "description": "Backend is running (MQTT or DB may still be degraded — check individual fields)",
            "content": {
                "application/json": {
                    "examples": {
                        "all_ok": {
                            "summary": "All systems operational",
                            "value": {
                                "status": "ok",
                                "version": "1.0.0",
                                "mqtt_connected": True,
                                "db_connected": True,
                                "ws_clients": 3,
                            },
                        },
                        "mqtt_down": {
                            "summary": "MQTT broker unreachable (REST API still works)",
                            "value": {
                                "status": "ok",
                                "version": "1.0.0",
                                "mqtt_connected": False,
                                "db_connected": True,
                                "ws_clients": 0,
                            },
                        },
                    }
                }
            },
        }
    },
)
async def health_check() -> dict:
    """Returns health status of all backend subsystems."""
    db_ok = False
    try:
        db = get_db()
        await db.command("ping")
        db_ok = True
    except Exception:
        pass

    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "mqtt_connected": mqtt_client.is_connected(),
        "db_connected": db_ok,
        "ws_clients": ws_manager.connected_count,
    }


# ---------------------------------------------------------------------------
# WebSocket endpoint (Session 0, Section 9)
# ---------------------------------------------------------------------------

@app.websocket("/ws/connect")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token obtained from POST /auth/login"),
) -> None:
    """
    **Real-time WebSocket connection for the dashboard.**

    Connect with: `ws://localhost:8000/ws/connect?token=<access_token>`

    The server pushes the following events (JSON envelope):
    - `device_registered` — new agent onboarded
    - `device_status` — heartbeat received
    - `new_log` — log entry received
    - `command_update` — command lifecycle transition
    - `alert_triggered` — alert condition met
    - `discovery_update` — network scan result
    - `agent_update_progress` — agent update step

    All messages follow:
    ```json
    { "event": "<name>", "data": { ... }, "timestamp": "ISO8601" }
    ```

    Close codes:
    - `4001` — invalid or expired JWT token
    - `1001` — server-side ping timeout (no pong within 60 s)
    """
    # Validation du token JWT
    try:
        db = get_db()
        user = await get_optional_ws_user(token, db)
    except Exception:
        await websocket.close(code=4001)
        return

    client_id = str(uuid.uuid4())
    await ws_manager.connect(client_id, websocket)
    logger.info("WebSocket connecté : user=%s client_id=%s", user.email, client_id)

    # Envoyer immédiatement le résultat SMTP stocké à ce nouveau client
    if _smtp_notification is not None:
        await ws_manager.send_to(client_id, "system_notification", _smtp_notification)

    try:
        while True:
            # Attente de messages (pong ou autres) — ping/pong géré par le client
            data = await websocket.receive_text()
            # On peut ignorer les messages entrants (pong) ou les traiter
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.info("WebSocket déconnecté : client_id=%s", client_id)
    except Exception as exc:
        logger.warning("Erreur WebSocket client_id=%s : %s", client_id, exc)
    finally:
        await ws_manager.disconnect(client_id)
