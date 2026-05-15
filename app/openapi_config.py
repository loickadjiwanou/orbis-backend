"""
OpenAPI / Swagger enrichment for Orbis Backend.

Provides:
  - Tag descriptions shown in Swagger UI and ReDoc sidebar
  - Request/response examples injected via custom_openapi()
  - Called once from main.py to override app.openapi
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

# ---------------------------------------------------------------------------
# Tag metadata — displayed in the Swagger UI and ReDoc sidebar
# ---------------------------------------------------------------------------

OPENAPI_TAGS: list[dict[str, Any]] = [
    {
        "name": "auth",
        "description": (
            "**Authentication** — JWT-based login, token refresh, and user profile. "
            "All protected endpoints require `Authorization: Bearer <access_token>`. "
            "Tokens are signed with HS256. Access tokens expire in 24 h, "
            "refresh tokens in 7 days."
        ),
    },
    {
        "name": "devices",
        "description": (
            "**Device management** — list, inspect, update, and revoke remote devices. "
            "Devices are automatically registered when an agent publishes on "
            "`devices/register` over MQTT. "
            "This router also exposes sub-resources: commands sent to a device "
            "(`POST /devices/{id}/command`) and its log history "
            "(`GET /devices/{id}/logs`)."
        ),
    },
    {
        "name": "groups",
        "description": (
            "**Device groups** — logical collections of devices. "
            "A device can belong to multiple groups. "
            "Commands can be broadcast to all devices in a group in one request. "
            "Alert scopes can target a group."
        ),
    },
    {
        "name": "commands",
        "description": (
            "**Command lookup** — retrieve a specific command by its UUID. "
            "Commands are created via `POST /devices/{id}/command` or "
            "`POST /groups/{id}/command`. "
            "The full lifecycle is: `pending → sent → acknowledged → executing → success | failed`."
        ),
    },
    {
        "name": "actions",
        "description": (
            "**Reusable actions** — parameterised command templates stored in MongoDB. "
            "Templates use `{{parameter_name}}` placeholders resolved at execution time. "
            "An action can be executed on a single device or an entire group."
        ),
    },
    {
        "name": "instructions",
        "description": (
            "**Multi-step workflows** — ordered sequences of actions executed "
            "sequentially on a device or group. "
            "Each step has a `condition_continuer` (`on_success` | `on_failure` | `always`) "
            "that controls whether the next step runs."
        ),
    },
    {
        "name": "alerts",
        "description": (
            "**Alert rules** — conditions evaluated on every incoming log. "
            "Three condition types: `log_level` (level equality), "
            "`metadata_threshold` (numeric comparison on log metadata fields), "
            "and `inactivity` (checked by a background job every 60 s). "
            "When triggered, an alert can fire an action and/or call a webhook."
        ),
    },
    {
        "name": "agent-versions",
        "description": (
            "**Agent version registry** — tracks binary releases per platform "
            "(`linux`, `macos`, `windows`, `android`). "
            "One version per platform can be marked `is_current=true` via "
            "`PATCH /agent-versions/{id}/set-current`.\n\n"
            "**OTA update flow:**\n"
            "1. Register a new binary: `POST /agent-versions` with version, platform, download URL, SHA-256 and changelog.\n"
            "2. Trigger the update on a device: `POST /devices/{id}/update` with `version_id`.\n"
            "3. Or trigger on a whole group: `POST /groups/{id}/update` with `version_id`.\n"
            "4. The backend publishes an `agent_update` command on `devices/{id}/commands` "
            "and the alias topic `devices/{id}/update` (QoS 1).\n"
            "5. The agent downloads the binary, verifies SHA-256, replaces the binary atomically, "
            "and restarts the service.\n"
            "6. Intermediate steps are reported on `devices/{id}/update_progress` (MQTT) "
            "and forwarded in real-time via WebSocket event `agent_update_progress`.\n"
            "7. The final result (`success` or `failed`) is published on `devices/{id}/results`."
        ),
    },
    {
        "name": "agent-logs",
        "description": (
            "**Agent internal logs** — low-level diagnostic logs emitted by the agent itself "
            "(spdlog on desktop, logcat/AgentLogger on Android) and forwarded to the backend "
            "via the MQTT topic `devices/{id}/agent_logs` (QoS 0).\n\n"
            "These are distinct from application logs (`/logs`): they reflect the agent's own "
            "internal operation (MQTT reconnections, command execution, update progress, etc.).\n\n"
            "Filterable by `device_id`, `plateforme`, `level` (DEBUG/INFO/WARNING/ERROR/CRITICAL), "
            "and free-text `search` on the message field."
        ),
    },
    {
        "name": "audit",
        "description": (
            "**Audit trail** — immutable log of all mutations (POST / PATCH / DELETE). "
            "Captures operator, action type, target device, IP address, and result. "
            "Exportable as CSV via `GET /audit/export`."
        ),
    },
    {
        "name": "discovery",
        "description": (
            "**Network discovery** — stores and exposes results of local network scans "
            "performed by agents (`devices/{id}/discovery` MQTT topic). "
            "Each scan reports neighboring hosts with their open TCP ports."
        ),
    },
    {
        "name": "system",
        "description": (
            "**System endpoints** — health check (`GET /health`) "
            "returning MongoDB and MQTT broker connectivity status, "
            "application version, and connected WebSocket client count."
        ),
    },
]

# ---------------------------------------------------------------------------
# Response examples injected into OpenAPI schemas
# ---------------------------------------------------------------------------

_EXAMPLES: dict[str, Any] = {
    # ── auth ────────────────────────────────────────────────────────────────
    "LoginRequest": {
        "summary": "Admin login",
        "value": {"email": "admin@orbis.local", "password": "admin_secret"},
    },
    "TokenResponse": {
        "summary": "Successful login",
        "value": {
            "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI1MDdm...",
            "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI1MDdm...",
            "token_type": "bearer",
            "expires_in": 86400,
            "user": {
                "id": "507f1f77bcf86cd799439011",
                "email": "admin@orbis.local",
                "full_name": "Administrator",
                "role": "admin",
                "is_active": True,
                "created_at": "2025-01-15T10:00:00Z",
                "last_login": "2025-01-15T10:30:00Z",
            },
        },
    },
    # ── device ──────────────────────────────────────────────────────────────
    "DevicePublic": {
        "summary": "Linux server device",
        "value": {
            "id": "507f1f77bcf86cd799439011",
            "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "nom": "prod-web-01",
            "hostname": "prod-web-01.example.com",
            "plateforme": "linux",
            "os_version": "Ubuntu 22.04.3 LTS",
            "architecture": "x86_64",
            "statut": "online",
            "version_agent": "1.0.0",
            "derniere_connexion": "2025-01-15T10:30:00Z",
            "config_logs": {"interval_sec": 60, "levels": ["INFO", "WARNING", "ERROR", "CRITICAL"], "sources": []},
            "groupe_ids": ["507f1f77bcf86cd799439020"],
            "revoked": False,
            "created_at": "2025-01-10T08:00:00Z",
            "metadata": {},
        },
    },
    "CommandCreate_shell": {
        "summary": "Shell command",
        "value": {"type": "shell", "payload": {"command": "df -h", "shell": "/bin/bash"}, "timeout_sec": 30},
    },
    "CommandCreate_collect": {
        "summary": "Force log collection",
        "value": {"type": "collect_now", "payload": {}},
    },
    "CommandCreate_scan": {
        "summary": "Network scan",
        "value": {"type": "scan_network", "payload": {}},
    },
    "CommandResponse": {
        "summary": "Command sent",
        "value": {
            "id": "507f1f77bcf86cd799439030",
            "command_id": "c1d2e3f4-a5b6-7890-cdef-123456789abc",
            "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "type": "shell",
            "payload": {"command": "df -h", "shell": "/bin/bash"},
            "timeout_sec": 30,
            "statut": "sent",
            "resultat": None,
            "error_message": None,
            "exit_code": None,
            "cree_le": "2025-01-15T10:30:00Z",
            "envoye_le": "2025-01-15T10:30:00.050Z",
            "acquitte_le": None,
            "execute_le": None,
            "termine_le": None,
            "cree_par": "507f1f77bcf86cd799439011",
        },
    },
    "CommandSuccess": {
        "summary": "Completed command",
        "value": {
            "id": "507f1f77bcf86cd799439030",
            "command_id": "c1d2e3f4-a5b6-7890-cdef-123456789abc",
            "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "type": "shell",
            "payload": {"command": "df -h"},
            "timeout_sec": 30,
            "statut": "success",
            "resultat": "Filesystem      Size  Used Avail Use% Mounted on\n/dev/sda1        50G   15G   35G  30% /\n",
            "error_message": None,
            "exit_code": 0,
            "cree_le": "2025-01-15T10:30:00Z",
            "envoye_le": "2025-01-15T10:30:00.050Z",
            "acquitte_le": "2025-01-15T10:30:00.200Z",
            "execute_le": "2025-01-15T10:30:00.250Z",
            "termine_le": "2025-01-15T10:30:00.500Z",
            "cree_par": "507f1f77bcf86cd799439011",
        },
    },
    # ── logs ────────────────────────────────────────────────────────────────
    "LogEntry": {
        "summary": "Disk warning log",
        "value": {
            "id": "507f1f77bcf86cd799439050",
            "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "timestamp": "2025-01-15T10:30:00Z",
            "level": "WARNING",
            "source": "disk_plugin",
            "message": "Disk usage high: 89%",
            "metadata": {"mount_point": "/", "used_gb": 178, "total_gb": 200, "percent": 89.0},
            "received_at": "2025-01-15T10:30:00.100Z",
        },
    },
    # ── groups ──────────────────────────────────────────────────────────────
    "GroupCreate": {
        "summary": "Create group",
        "value": {"nom": "Production Servers", "description": "All production Linux servers"},
    },
    "GroupDevicesAdd": {
        "summary": "Add two devices",
        "value": {"device_ids": ["a1b2c3d4-e5f6-7890-abcd-ef1234567890", "b2c3d4e5-f6a7-8901-bcde-f12345678901"]},
    },
    # ── actions ─────────────────────────────────────────────────────────────
    "ActionCreate": {
        "summary": "Restart systemd service",
        "value": {
            "nom": "Restart Service",
            "description": "Restart a named systemd service",
            "type": "shell",
            "script_template": "systemctl restart {{service_name}}",
            "parametres": [
                {
                    "nom": "service_name",
                    "type": "string",
                    "requis": True,
                    "description": "Name of the systemd service to restart",
                    "valeur_defaut": None,
                    "enum_values": ["nginx", "postgresql", "redis"],
                }
            ],
            "compatible_plateformes": ["linux"],
        },
    },
    "ActionExecute": {
        "summary": "Execute on a device",
        "value": {
            "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "parametres": {"service_name": "nginx"},
        },
    },
    # ── alerts ──────────────────────────────────────────────────────────────
    "AlertCreate_disk": {
        "summary": "Disk usage threshold alert",
        "value": {
            "nom": "Disk Critical",
            "scope": "device",
            "scope_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "condition": {
                "type": "metadata_threshold",
                "valeur": 90,
                "operateur": "gt",
                "metadata_key": "percent",
            },
            "action_id": None,
            "webhook_url": "https://hooks.slack.com/services/T00/B00/xxx",
        },
    },
    "AlertCreate_error": {
        "summary": "Error log level alert",
        "value": {
            "nom": "Any Error Log",
            "scope": "group",
            "scope_id": "507f1f77bcf86cd799439020",
            "condition": {
                "type": "log_level",
                "valeur": "ERROR",
                "operateur": "eq",
                "metadata_key": None,
            },
            "action_id": "507f1f77bcf86cd799439011",
            "webhook_url": None,
        },
    },
    # ── agent versions ───────────────────────────────────────────────────────
    "AgentVersionCreate": {
        "summary": "New Linux release",
        "value": {
            "version": "1.2.0",
            "plateforme": "linux",
            "url_download": "https://releases.orbis.io/agent/1.2.0/orbis-agent-linux-x86_64",
            "hash_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "changelog": "Fix memory leak in LogCollector, improve reconnection logic",
        },
    },
    "AgentVersionCreate_android": {
        "summary": "New Android release",
        "value": {
            "version": "1.2.0",
            "plateforme": "android",
            "url_download": "https://releases.orbis.io/agent/1.2.0/orbis-agent-1.2.0.apk",
            "hash_sha256": "a3f8c2d1e4b5f67890abcdef1234567890abcdef1234567890abcdef12345678",
            "changelog": "Add SHA-256 verification for APK install, improve MQTT reconnection",
            "date_release": "2025-01-15T10:00:00Z",
        },
    },
    "AgentVersionResponse": {
        "summary": "Registered version",
        "value": {
            "id": "507f1f77bcf86cd799439099",
            "version": "1.2.0",
            "plateforme": "linux",
            "url_download": "https://releases.orbis.io/agent/1.2.0/orbis-agent-linux-x86_64",
            "hash_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "changelog": "Fix memory leak in LogCollector, improve reconnection logic",
            "date_release": "2025-01-15T10:00:00Z",
            "is_current": False,
            "uploaded_by": "507f1f77bcf86cd799439011",
        },
    },
    "AgentUpdateTrigger": {
        "summary": "Trigger OTA update",
        "value": {"version_id": "507f1f77bcf86cd799439099"},
    },
    "AgentUpdateCommand": {
        "summary": "agent_update command created",
        "value": {
            "id": "507f1f77bcf86cd799439031",
            "command_id": "d2e3f4a5-b6c7-8901-defa-234567890bcd",
            "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "type": "agent_update",
            "payload": {
                "version": "1.2.0",
                "url": "https://releases.orbis.io/agent/1.2.0/orbis-agent-linux-x86_64",
                "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "changelog": "Fix memory leak in LogCollector, improve reconnection logic",
            },
            "timeout_sec": 300,
            "statut": "sent",
            "resultat": None,
            "error_message": None,
            "exit_code": None,
            "cree_le": "2025-01-15T10:30:00Z",
            "envoye_le": "2025-01-15T10:30:00.050Z",
            "acquitte_le": None,
            "execute_le": None,
            "termine_le": None,
            "cree_par": "507f1f77bcf86cd799439011",
        },
    },
    "AgentUpdateProgress_downloading": {
        "summary": "WebSocket — downloading step",
        "value": {
            "event": "agent_update_progress",
            "data": {
                "command_id": "d2e3f4a5-b6c7-8901-defa-234567890bcd",
                "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "statut": "executing",
                "step": "downloading",
                "output": "Downloading https://releases.orbis.io/agent/1.2.0/orbis-agent-linux-x86_64",
                "error": None,
            },
            "timestamp": "2025-01-15T10:30:01Z",
        },
    },
    "AgentUpdateProgress_success": {
        "summary": "WebSocket — success step",
        "value": {
            "event": "agent_update_progress",
            "data": {
                "command_id": "d2e3f4a5-b6c7-8901-defa-234567890bcd",
                "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "statut": "executing",
                "step": "success",
                "output": "Updated to 1.2.0 successfully",
                "error": None,
            },
            "timestamp": "2025-01-15T10:30:45Z",
        },
    },
    "AgentUpdateProgress_rollback": {
        "summary": "WebSocket — rollback step (update failed)",
        "value": {
            "event": "agent_update_progress",
            "data": {
                "command_id": "d2e3f4a5-b6c7-8901-defa-234567890bcd",
                "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "statut": "executing",
                "step": "rollback",
                "output": None,
                "error": "SHA256 mismatch — rolled back to previous version",
            },
            "timestamp": "2025-01-15T10:30:10Z",
        },
    },
    # ── agent logs ───────────────────────────────────────────────────────────
    "AgentLogEntry": {
        "summary": "Agent internal log entry",
        "value": {
            "id": "507f1f77bcf86cd799439070",
            "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "timestamp": "2025-01-15T10:30:00Z",
            "level": "INFO",
            "source": "CommandExecutor",
            "message": "Command received: d2e3f4a5 (type=agent_update)",
        },
    },
    "AgentLogEntry_error": {
        "summary": "Agent error log entry",
        "value": {
            "id": "507f1f77bcf86cd799439071",
            "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "timestamp": "2025-01-15T10:30:05Z",
            "level": "ERROR",
            "source": "UpdateManager",
            "message": "SHA256 mismatch. expected=e3b0... got=a1b2...",
        },
    },
    # ── discovery ────────────────────────────────────────────────────────────
    "DiscoveryResult": {
        "summary": "Network scan result",
        "value": {
            "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "timestamp": "2025-01-15T10:30:00Z",
            "network": "192.168.1.0/24",
            "neighbors": [
                {"ip": "192.168.1.1", "hostname": "router.local", "open_ports": [80, 443], "response_time_ms": 1},
                {"ip": "192.168.1.10", "hostname": "server01.local", "open_ports": [22, 80, 443], "response_time_ms": 3},
                {"ip": "192.168.1.42", "hostname": "", "open_ports": [22], "response_time_ms": 8},
            ],
        },
    },
    # ── health ───────────────────────────────────────────────────────────────
    "HealthOk": {
        "summary": "All systems operational",
        "value": {
            "status": "ok",
            "version": "1.0.0",
            "mqtt_connected": True,
            "db_connected": True,
            "ws_clients": 3,
        },
    },
    "HealthDegraded": {
        "summary": "MQTT disconnected (broker unreachable)",
        "value": {
            "status": "ok",
            "version": "1.0.0",
            "mqtt_connected": False,
            "db_connected": True,
            "ws_clients": 0,
        },
    },
}


# ---------------------------------------------------------------------------
# custom_openapi() — monkey-patches app.openapi to inject all enrichments
# ---------------------------------------------------------------------------

def setup_openapi(app: FastAPI) -> None:
    """
    Overrides the default app.openapi() to inject tag descriptions
    and request/response examples into the generated OpenAPI schema.

    Call once from main.py after all routers are registered:
        setup_openapi(app)
    """

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title="Orbis Backend API",
            version="1.0.0",
            description=_API_DESCRIPTION,
            routes=app.routes,
            tags=OPENAPI_TAGS,
        )

        # Inject examples into schemas
        _inject_examples(schema)

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def _inject_examples(schema: dict) -> None:
    """Walk the OpenAPI schema and inject examples into component schemas."""
    components = schema.get("components", {})
    schemas = components.get("schemas", {})

    _example_map = {
        "LoginRequest": _EXAMPLES["LoginRequest"]["value"],
        "GroupCreate": _EXAMPLES["GroupCreate"]["value"],
        "ActionCreate": _EXAMPLES["ActionCreate"]["value"],
        "AgentVersionCreate": _EXAMPLES["AgentVersionCreate"]["value"],
    }

    for schema_name, example in _example_map.items():
        if schema_name in schemas:
            schemas[schema_name]["example"] = example

    paths = schema.get("paths", {})

    # Add examples to /health response
    health_path = paths.get("/health", {})
    health_get = health_path.get("get", {})
    health_responses = health_get.get("responses", {})
    ok_response = health_responses.get("200", {})
    ok_content = ok_response.get("content", {})
    json_content = ok_content.get("application/json", {})
    if json_content is not None:
        json_content["examples"] = {
            "operational": _EXAMPLES["HealthOk"],
            "mqtt_down": _EXAMPLES["HealthDegraded"],
        }

    # Add examples to POST /devices/{device_id}/update
    _inject_path_examples(
        paths,
        "/devices/{device_id}/update",
        "post",
        request_examples={"trigger_ota": _EXAMPLES["AgentUpdateTrigger"]},
        response_examples={"command_created": _EXAMPLES["AgentUpdateCommand"]},
    )

    # Add examples to POST /groups/{group_id}/update
    _inject_path_examples(
        paths,
        "/groups/{group_id}/update",
        "post",
        request_examples={"trigger_ota": _EXAMPLES["AgentUpdateTrigger"]},
    )

    # Add examples to POST /agent-versions
    _inject_path_examples(
        paths,
        "/agent-versions",
        "post",
        request_examples={
            "linux_release": _EXAMPLES["AgentVersionCreate"],
            "android_release": _EXAMPLES["AgentVersionCreate_android"],
        },
        response_examples={"created": _EXAMPLES["AgentVersionResponse"]},
    )


def _inject_path_examples(
    paths: dict,
    path: str,
    method: str,
    request_examples: dict | None = None,
    response_examples: dict | None = None,
) -> None:
    """Helper: inject request body and response examples into a specific path/method."""
    path_item = paths.get(path, {})
    operation = path_item.get(method, {})
    if not operation:
        return

    if request_examples:
        request_body = operation.setdefault("requestBody", {})
        content = request_body.setdefault("content", {})
        json_ct = content.setdefault("application/json", {})
        json_ct["examples"] = request_examples

    if response_examples:
        responses = operation.get("responses", {})
        for status_code in ("200", "201"):
            resp = responses.get(status_code)
            if resp:
                content = resp.setdefault("content", {})
                json_ct = content.setdefault("application/json", {})
                json_ct["examples"] = response_examples
                break


# ---------------------------------------------------------------------------
# Long-form API description (shown in Swagger UI header and ReDoc intro)
# ---------------------------------------------------------------------------

_API_DESCRIPTION = """
## Overview

**Orbis** is a device supervision and remote control platform.
This API is the central hub between:

- **MQTT agents** (Linux/Windows/macOS/Android) that send telemetry and receive commands
- **MongoDB** for persistent storage of devices, logs, commands, alerts, and audit trails
- **The Electron dashboard** that consumes the REST API and real-time WebSocket events

---

## Authentication

All endpoints except `/auth/login`, `/auth/refresh`, and `/health` require a **JWT Bearer token**.

```
Authorization: Bearer <access_token>
```

Obtain tokens via `POST /auth/login`. Access tokens expire in **24 hours**;
use `POST /auth/refresh` with the refresh token (valid **7 days**) to obtain a new one.

### Roles

| Role | Capabilities |
|------|-------------|
| `admin` | Full access including user management |
| `operator` | Read + write on all resources except user accounts |
| `viewer` | Read-only access |

---

## Real-time WebSocket

Connect to `ws://localhost:8000/ws/connect?token=<access_token>` to receive
server-push events. All events follow the envelope:

```json
{ "event": "device_status", "data": { ... }, "timestamp": "2025-01-15T10:30:00Z" }
```

**Events:**

| Event | Trigger |
|-------|---------|
| `device_registered` | New agent onboarded |
| `device_status` | Heartbeat received or inactivity timeout |
| `new_log` | Log entry received from agent |
| `command_update` | Command lifecycle transition (`acknowledged` → `executing` → `success`/`failed`) |
| `alert_triggered` | Alert condition matched |
| `discovery_update` | Network scan result received |
| `agent_update_progress` | OTA update step published by agent (`downloading` · `verifying` · `replacing` · `restarting` · `success` · `rollback`) |
| `system_notification` | SMTP configuration check result at startup |

---

## OTA Agent Update Flow

```
Dashboard                    Backend                      Agent
   |                            |                           |
   |-- POST /agent-versions --> |                           |
   |<-- 201 AgentVersion ------ |                           |
   |                            |                           |
   |-- POST /devices/{id}/update|                           |
   |   { version_id }           |                           |
   |                            |-- MQTT agent_update ----> |
   |                            |   devices/{id}/commands   |
   |                            |   devices/{id}/update     |
   |                            |                           |-- download binary
   |                            |<-- MQTT update_progress --|   step: downloading
   |<-- WS agent_update_progress|                           |-- verify SHA-256
   |                            |<-- MQTT update_progress --|   step: verifying
   |<-- WS agent_update_progress|                           |-- replace binary
   |                            |<-- MQTT update_progress --|   step: replacing
   |<-- WS agent_update_progress|                           |-- restart service
   |                            |<-- MQTT results ----------|   statut: success
   |<-- WS command_update ------ |                           |
```

---

## MQTT Contract

Agents communicate exclusively through the **EMQX 5.x** broker.
See `docs/SESSION_0_CONTRAT_INTERFACES.md` for the complete JSON contracts.

**Backend subscribes to:**
- `devices/register` — agent first onboarding
- `devices/+/logs` — application log batches (QoS 1)
- `devices/+/status` — heartbeats, retained every 30 s (QoS 1)
- `devices/+/results` — command lifecycle updates (QoS 1)
- `devices/+/discovery` — network scan results (QoS 1)
- `devices/+/agent_logs` — agent internal diagnostic logs (QoS 0)
- `devices/+/update_progress` — OTA update step notifications (QoS 1)

**Backend publishes to:**
- `devices/{id}/register_ack` — onboarding response
- `devices/{id}/commands` — commands to execute
- `devices/{id}/update` — agent update trigger (alias of commands, QoS 1)

---

## Standard error format

```json
{ "detail": "Device not found", "code": "DEVICE_NOT_FOUND" }
```

**Error codes:** `DEVICE_NOT_FOUND` · `DEVICE_REVOKED` · `COMMAND_NOT_FOUND` ·
`INVALID_PAYLOAD` · `UNAUTHORIZED` · `FORBIDDEN` · `MQTT_PUBLISH_FAILED` · `EMQX_API_ERROR`
"""
