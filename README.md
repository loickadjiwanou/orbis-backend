# Orbis Backend

FastAPI-based backend for the Orbis device supervision and remote control platform. It acts as the central hub between MQTT agents (Linux/Windows/macOS/Android), MongoDB, and the Electron dashboard.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Quick Start](#quick-start)
5. [Configuration Reference](#configuration-reference)
6. [Authentication](#authentication)
7. [REST API Reference](#rest-api-reference)
8. [MQTT Protocol](#mqtt-protocol)
9. [WebSocket Events](#websocket-events)
10. [Data Models](#data-models)
11. [RBAC — Roles & Permissions](#rbac--roles--permissions)
12. [Testing](#testing)
13. [Docker Deployment](#docker-deployment)
14. [Project Structure](#project-structure)
15. [Development Guide](#development-guide)
16. [Troubleshooting](#troubleshooting)

---

## Overview

The Orbis Backend serves four main responsibilities:

| Responsibility | Mechanism |
|---|---|
| Receive telemetry and logs from agents | MQTT subscriber (aiomqtt) |
| Send commands to agents | MQTT publisher |
| Expose a REST API to the dashboard | FastAPI |
| Push real-time events to the dashboard | WebSocket |

All agent ↔ backend communication is exclusive through the **EMQX 5.x** MQTT broker over TLS (port 8883). The dashboard connects via **WebSocket** (`/ws/connect`) and REST.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  orbis-agent-desktop (C++17)    orbis-agent-android (Kotlin)                │
│          │                        │                             │
│          └──────────┬─────────────┘                            │
│                     │ MQTT/TLS :8883                            │
│                     ▼                                           │
│             ┌──────────────┐                                    │
│             │  EMQX 5.x   │◄──── Backend publishes commands     │
│             └──────┬───────┘                                    │
│                    │ MQTT subscriber                            │
│                    ▼                                            │
│         ┌─────────────────────┐                                 │
│         │   FastAPI Backend   │◄──── REST API (dashboard)       │
│         │   (this service)    │────► WebSocket /ws/connect      │
│         └──────────┬──────────┘                                 │
│                    │ Motor async                                 │
│                    ▼                                            │
│              ┌──────────┐                                       │
│              │ MongoDB  │                                        │
│              └──────────┘                                       │
└─────────────────────────────────────────────────────────────────┘
```

### Internal Component Map

```
app/
├── main.py              FastAPI app, lifespan, WS endpoint, /health
├── config.py            All settings via pydantic-settings + .env
├── database.py          Motor async client, MongoDB indexes
├── models/              Pydantic v2 data models (one per collection)
├── routers/             FastAPI routers (one file per resource)
├── services/            Business logic (stateless async functions)
├── mqtt/
│   ├── client.py        aiomqtt loop, TLS, exponential backoff
│   └── handlers.py      One handler per MQTT topic
├── websocket/
│   └── manager.py       WebSocketManager (broadcast / send_to)
└── middleware/
    ├── auth.py           JWT decode, get_current_user, require_role
    └── audit.py          Starlette middleware — logs all mutations
```

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | ≥ 3.11 | `python3 --version` |
| MongoDB | ≥ 7.0 | Running on port 27017 |
| EMQX | 5.5.1 | Required for full MQTT support |
| pip | latest | `pip install --upgrade pip` |

> **Development without EMQX:** The backend starts and the REST API works fully without EMQX. The MQTT client retries in the background with exponential backoff. `GET /health` will show `"mqtt_connected": false` but all REST endpoints remain functional.

---

## Quick Start

### 1 — Clone & install

```bash
cd backend/

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 2 — Configure

```bash
cp .env.example .env
```

Minimum configuration for **local development without TLS**:

```env
MONGODB_URL=mongodb://localhost:27017
MONGODB_DB=orbis
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
MQTT_USERNAME=backend
MQTT_PASSWORD=dev_password
MQTT_USE_TLS=false
EMQX_API_URL=http://localhost:18083
EMQX_API_ID=your_emqx_api_key_id
EMQX_API_KEY=your_emqx_api_key_secret
MQTT_REGISTER_SECRET=orbis_register_secret
JWT_SECRET_KEY=dev_secret_minimum_32_chars_long
ADMIN_EMAIL=admin@orbis.local
ADMIN_PASSWORD=admin_secret
```

### 3 — Start

```bash
# Development (auto-reload)
uvicorn app.main:app --reload --port 8000

# Production (4 workers)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 4 — Verify

```bash
curl http://localhost:8000/health
# → {"status":"ok","version":"1.0.0","mqtt_connected":true,"db_connected":true,"ws_clients":0}
```

Interactive API docs:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

---

## Configuration Reference

All settings are loaded from environment variables (or a `.env` file at the working directory). Every variable has a default except those marked **required**.

### MongoDB

| Variable | Default | Description |
|---|---|---|
| `MONGODB_URL` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGODB_DB` | `orbis` | Database name |

### MQTT Broker

| Variable | Default | Description |
|---|---|---|
| `MQTT_BROKER_HOST` | `localhost` | EMQX hostname |
| `MQTT_BROKER_PORT` | `8883` | TLS port (use `1883` for dev without TLS) |
| `MQTT_USERNAME` | `backend` | EMQX account username |
| `MQTT_PASSWORD` | — | **Required** — EMQX account password |
| `MQTT_USE_TLS` | `true` | Disable for local dev (`false`) |
| `MQTT_CA_CERT` | `null` | Path to CA certificate for TLS verification |
| `MQTT_REGISTER_SECRET` | — | **Required** — Shared secret for agent first onboarding |

### EMQX Admin API

| Variable | Default | Description |
|---|---|---|
| `EMQX_API_URL` | `http://emqx:18083` | EMQX REST admin base URL |
| `EMQX_API_ID` | — | **Required** — AppID from EMQX → System → API Keys |
| `EMQX_API_KEY` | — | **Required** — AppSecret from EMQX → System → API Keys |

### JWT

| Variable | Default | Description |
|---|---|---|
| `JWT_SECRET_KEY` | — | **Required** — Min 32 chars random string |
| `JWT_ALGORITHM` | `HS256` | Signing algorithm |
| `ACCESS_TOKEN_EXPIRE_HOURS` | `24` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime |

### Application

| Variable | Default | Description |
|---|---|---|
| `CORS_ORIGINS` | `["http://localhost:5173"]` | Allowed CORS origins |
| `LOG_RETENTION_DAYS` | `90` | MongoDB TTL for log documents |
| `ADMIN_EMAIL` | `admin@orbis.local` | Default admin email (created on first start) |
| `ADMIN_PASSWORD` | — | **Required** — Default admin password |
| `ADMIN_FULL_NAME` | `Administrator` | Default admin display name |
| `APP_VERSION` | `1.0.0` | Application version (shown in /health) |

---

## Authentication

The backend uses **JWT Bearer tokens** with HS256 signing.

### Login flow

```bash
# 1. Obtain tokens
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@orbis.local", "password": "admin_secret"}'

# Response:
{
  "access_token": "eyJhbGci...",
  "refresh_token": "eyJhbGci...",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": { "id": "...", "email": "...", "role": "admin", ... }
}

# 2. Use access token on protected endpoints
curl http://localhost:8000/devices \
  -H "Authorization: Bearer eyJhbGci..."

# 3. Refresh when access token expires
curl -X POST http://localhost:8000/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "eyJhbGci..."}'
```

### JWT Payload

```json
{
  "sub": "507f1f77bcf86cd799439011",
  "email": "admin@orbis.local",
  "role": "admin",
  "iat": 1705312200,
  "exp": 1705398600
}
```

### WebSocket authentication

The WebSocket endpoint uses a **query parameter** (not a header, as per browser WebSocket API limitations):

```
ws://localhost:8000/ws/connect?token=<JWT_ACCESS_TOKEN>
```

If the token is invalid or expired, the connection is closed with code `4001`.

---

## REST API Reference

**Base URL:** `http://localhost:8000`  
**Auth header:** `Authorization: Bearer {access_token}`  
**Content-Type:** `application/json`

### Standard response formats

**Paginated list:**
```json
{
  "items": [ ... ],
  "total": 142,
  "skip": 0,
  "limit": 50
}
```

**Error:**
```json
{
  "detail": "Device not found",
  "code": "DEVICE_NOT_FOUND"
}
```

**Error codes:** `DEVICE_NOT_FOUND` · `DEVICE_REVOKED` · `COMMAND_NOT_FOUND` · `INVALID_PAYLOAD` · `UNAUTHORIZED` · `FORBIDDEN` · `MQTT_PUBLISH_FAILED` · `EMQX_API_ERROR`

---

### `/auth` — Authentication

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `POST` | `/auth/login` | Obtain JWT tokens | ✗ |
| `POST` | `/auth/refresh` | Refresh access token | ✗ |
| `POST` | `/auth/logout` | Logout (client-side) | ✓ |
| `GET` | `/auth/me` | Current user profile | ✓ |

#### `POST /auth/login`

```json
// Request
{ "email": "admin@orbis.local", "password": "admin_secret" }

// Response 200
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": {
    "id": "507f1f77bcf86cd799439011",
    "email": "admin@orbis.local",
    "full_name": "Administrator",
    "role": "admin",
    "is_active": true,
    "created_at": "2025-01-15T10:00:00Z",
    "last_login": "2025-01-15T10:30:00Z"
  }
}

// Response 401
{ "detail": "Invalid credentials", "code": "UNAUTHORIZED" }
```

---

### `/devices` — Device Management

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `GET` | `/devices` | List devices (paginated) | viewer+ |
| `GET` | `/devices/{device_id}` | Get device detail | viewer+ |
| `PATCH` | `/devices/{device_id}` | Update device metadata | operator+ |
| `DELETE` | `/devices/{device_id}` | Revoke device | operator+ |
| `POST` | `/devices/{device_id}/command` | Send command | operator+ |
| `POST` | `/devices/{device_id}/update` | Trigger agent update | operator+ |
| `GET` | `/devices/{device_id}/logs` | List device logs | viewer+ |
| `GET` | `/devices/{device_id}/commands` | List device commands | viewer+ |

#### `GET /devices`

Query parameters:

| Param | Type | Description |
|-------|------|-------------|
| `groupe_id` | string | Filter by group ID |
| `statut` | string | `online` \| `offline` \| `unknown` \| `revoked` |
| `plateforme` | string | `linux` \| `windows` \| `macos` \| `android` |
| `search` | string | Full-text search on `nom` and `hostname` |
| `skip` | int | Pagination offset (default: 0) |
| `limit` | int | Page size (default: 50, max: 500) |

```bash
curl "http://localhost:8000/devices?statut=online&plateforme=linux&limit=20" \
  -H "Authorization: Bearer $TOKEN"
```

#### `POST /devices/{device_id}/command`

Sends a command to a device via MQTT (`devices/{device_id}/commands`).

```json
// Request
{
  "type": "shell",
  "payload": { "command": "df -h", "shell": "/bin/bash" },
  "timeout_sec": 30
}

// Response 201
{
  "id": "507f1f77bcf86cd799439011",
  "command_id": "c1d2e3f4-a5b6-7890-cdef-123456789abc",
  "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "type": "shell",
  "payload": { "command": "df -h", "shell": "/bin/bash" },
  "timeout_sec": 30,
  "statut": "sent",
  "resultat": null,
  "error_message": null,
  "exit_code": null,
  "cree_le": "2025-01-15T10:30:00Z",
  "envoye_le": "2025-01-15T10:30:00.050Z",
  "acquitte_le": null,
  "execute_le": null,
  "termine_le": null,
  "cree_par": "507f1f77bcf86cd799439011"
}
```

**Supported command types:**

| Type | Payload fields | Description |
|------|---------------|-------------|
| `shell` | `command`, `shell` (opt.) | Execute shell command |
| `restart_service` | _(empty)_ | Restart orbis-agent service |
| `collect_now` | _(empty)_ | Force immediate log collection |
| `scan_network` | _(empty)_ | Trigger network discovery scan |
| `get_info` | _(empty)_ | Return full device info |
| `agent_update` | `version`, `url`, `sha256`, `changelog` | Update agent binary |

#### `GET /devices/{device_id}/logs`

Query parameters:

| Param | Type | Description |
|-------|------|-------------|
| `level` | string | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL` |
| `source` | string | Exact source filter (e.g. `disk_plugin`) |
| `search` | string | Regex search in `message` field |
| `start_date` | ISO8601 | Lower bound on `timestamp` |
| `end_date` | ISO8601 | Upper bound on `timestamp` |
| `skip` | int | Pagination offset |
| `limit` | int | Page size (default: 100, max: 1000) |

---

### `/groups` — Device Groups

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `GET` | `/groups` | List all groups | viewer+ |
| `POST` | `/groups` | Create group | operator+ |
| `GET` | `/groups/{group_id}` | Get group | viewer+ |
| `PATCH` | `/groups/{group_id}` | Update group | operator+ |
| `DELETE` | `/groups/{group_id}` | Delete group | operator+ |
| `POST` | `/groups/{group_id}/devices` | Add devices to group | operator+ |
| `DELETE` | `/groups/{group_id}/devices` | Remove devices from group | operator+ |
| `POST` | `/groups/{group_id}/command` | Send command to all devices in group | operator+ |

```json
// POST /groups
{ "nom": "Production Servers", "description": "All production Linux servers" }

// POST /groups/{id}/devices — add devices
{ "device_ids": ["a1b2c3d4-...", "b2c3d4e5-..."] }

// POST /groups/{id}/command — broadcast command
{ "type": "collect_now", "payload": {} }
```

---

### `/commands` — Command Lookup

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `GET` | `/commands/{command_id}` | Get command by UUID | viewer+ |

**Command lifecycle states:**

```
pending → sent → acknowledged → executing → success
                                          → failed
```

---

### `/actions` — Reusable Actions

Actions are reusable command templates with `{{parameter}}` placeholders.

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `GET` | `/actions` | List active actions | viewer+ |
| `POST` | `/actions` | Create action | operator+ |
| `GET` | `/actions/{action_id}` | Get action | viewer+ |
| `PATCH` | `/actions/{action_id}` | Update action | operator+ |
| `DELETE` | `/actions/{action_id}` | Deactivate action | operator+ |
| `POST` | `/actions/{action_id}/execute` | Execute on device or group | operator+ |

```json
// POST /actions
{
  "nom": "Restart Service",
  "description": "Restart a systemd service",
  "type": "shell",
  "script_template": "systemctl restart {{service_name}}",
  "parametres": [
    {
      "nom": "service_name",
      "type": "string",
      "requis": true,
      "description": "Name of the systemd service",
      "valeur_defaut": null,
      "enum_values": null
    }
  ],
  "compatible_plateformes": ["linux"]
}

// POST /actions/{id}/execute
{
  "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "parametres": { "service_name": "nginx" }
}
```

---

### `/instructions` — Multi-Step Workflows

Instructions chain multiple actions into sequential workflows.

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `GET` | `/instructions` | List active instructions | viewer+ |
| `POST` | `/instructions` | Create instruction | operator+ |
| `GET` | `/instructions/{id}` | Get instruction | viewer+ |
| `PATCH` | `/instructions/{id}` | Update instruction | operator+ |
| `DELETE` | `/instructions/{id}` | Deactivate instruction | operator+ |
| `POST` | `/instructions/{id}/execute` | Execute on device or group | operator+ |

```json
// POST /instructions
{
  "nom": "Deploy & Verify",
  "description": "Pull latest code, restart services, check status",
  "trigger": "manual",
  "etapes": [
    {
      "ordre": 1,
      "action_id": "507f1f77bcf86cd799439011",
      "parametres": {},
      "condition_continuer": "on_success",
      "timeout_sec": 120
    },
    {
      "ordre": 2,
      "action_id": "507f1f77bcf86cd799439012",
      "parametres": { "service_name": "app" },
      "condition_continuer": "always",
      "timeout_sec": 30
    }
  ]
}
```

**Step conditions:**
- `on_success` — next step only if previous succeeded
- `on_failure` — next step only if previous failed
- `always` — always run next step

---

### `/alerts` — Alert Rules

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `GET` | `/alerts` | List alerts | viewer+ |
| `POST` | `/alerts` | Create alert | operator+ |
| `GET` | `/alerts/{alert_id}` | Get alert | viewer+ |
| `PATCH` | `/alerts/{alert_id}` | Update alert | operator+ |
| `DELETE` | `/alerts/{alert_id}` | Delete alert | operator+ |
| `POST` | `/alerts/{alert_id}/toggle` | Enable/disable | operator+ |

```json
// POST /alerts — trigger on disk > 90%
{
  "nom": "Disk Critical",
  "scope": "device",
  "scope_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "condition": {
    "type": "metadata_threshold",
    "valeur": 90,
    "operateur": "gt",
    "metadata_key": "percent"
  },
  "action_id": null,
  "webhook_url": "https://hooks.slack.com/services/..."
}
```

**Condition types:**

| Type | Description | Operateurs |
|------|-------------|-----------|
| `log_level` | Triggers when a log with matching level is received | `eq` |
| `metadata_threshold` | Triggers when `log.metadata[key]` satisfies condition | `eq` `gt` `lt` `gte` `lte` `contains` |
| `inactivity` | Triggers when device hasn't sent a heartbeat (background job) | — |

---

### `/alerts` — Alert Rules (continued)

**Scope:**
- `device` — applies to a single device (`scope_id` = `device_id`)
- `group` — applies to all devices in a group (`scope_id` = MongoDB group `_id`)

---

### `/agent-versions` — Agent Version Registry

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `GET` | `/agent-versions` | List versions (filter: `?plateforme=linux`) | viewer+ |
| `POST` | `/agent-versions` | Register new version | operator+ |
| `GET` | `/agent-versions/{id}` | Get version | viewer+ |
| `PATCH` | `/agent-versions/{id}/set-current` | Set as current for platform | operator+ |
| `DELETE` | `/agent-versions/{id}` | Delete version | operator+ |

```json
// POST /agent-versions
{
  "version": "1.2.0",
  "plateforme": "linux",
  "url_download": "https://releases.orbis.io/agent/1.2.0/orbis-agent-linux-x86_64",
  "hash_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "changelog": "Fix memory leak in LogCollector, improve reconnection logic"
}
```

---

### `/audit` — Audit Trail

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `GET` | `/audit` | Query audit logs (paginated) | viewer+ |
| `GET` | `/audit/export` | Export audit logs as CSV | viewer+ |

Query parameters for `/audit`:

| Param | Description |
|-------|-------------|
| `operateur_id` | Filter by operator user ID |
| `action_type` | e.g. `SEND_COMMAND`, `REVOKE_DEVICE` |
| `device_id` | Filter by device UUID |
| `start_date` | ISO8601 lower bound |
| `end_date` | ISO8601 upper bound |
| `skip` / `limit` | Pagination |

**Tracked action types:**

`CREATE_DEVICE` · `UPDATE_DEVICE` · `REVOKE_DEVICE` · `SEND_COMMAND` · `TRIGGER_UPDATE` · `CREATE_GROUP` · `UPDATE_GROUP` · `DELETE_GROUP` · `GROUP_COMMAND` · `CREATE_ACTION` · `EXECUTE_ACTION` · `CREATE_INSTRUCTION` · `EXECUTE_INSTRUCTION` · `CREATE_ALERT` · `DELETE_ALERT` · `UPLOAD_AGENT_VERSION`

---

### `/discovery` — Network Discovery

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `GET` | `/discovery/{device_id}` | Get latest scan result | viewer+ |

```json
// Response
{
  "device_id": "a1b2c3d4-...",
  "timestamp": "2025-01-15T10:30:00Z",
  "network": "192.168.1.0/24",
  "neighbors": [
    { "ip": "192.168.1.1", "hostname": "router.local", "open_ports": [80, 443], "response_time_ms": 1 },
    { "ip": "192.168.1.10", "hostname": "server01.local", "open_ports": [22, 80], "response_time_ms": 3 }
  ]
}
```

---

### `/health` — System Health

```bash
GET /health
# No authentication required

{
  "status": "ok",
  "version": "1.0.0",
  "mqtt_connected": true,
  "db_connected": true,
  "ws_clients": 3
}
```

---

## MQTT Protocol

The backend subscribes to the following topics on startup:

| Topic | Direction | QoS | Retained | Handler |
|-------|-----------|-----|----------|---------|
| `devices/register` | Agent → Backend | 1 | No | `handle_register` |
| `devices/{id}/logs` | Agent → Backend | 1 | No | `handle_logs` |
| `devices/{id}/status` | Agent → Backend | 1 | **Yes** | `handle_status` |
| `devices/{id}/results` | Agent → Backend | 1 | No | `handle_results` |
| `devices/{id}/discovery` | Agent → Backend | 1 | No | `handle_discovery` |

The backend publishes to:

| Topic | Direction | QoS | Retained |
|-------|-----------|-----|----------|
| `devices/{id}/register_ack` | Backend → Agent | 1 | No |
| `devices/{id}/commands` | Backend → Agent | 1 | No |
| `devices/{id}/update` | Backend → Agent | 1 | No |

### Agent Registration (Onboarding)

```
Agent                          EMQX                    Backend
  │                              │                        │
  │── CONNECT (user=register) ──►│                        │
  │── PUBLISH devices/register ─►│── forward ────────────►│
  │                              │                        │── create Device in MongoDB
  │                              │                        │── generate token
  │                              │                        │── create MQTT account in EMQX
  │◄── PUBLISH register_ack ─────│◄── publish ────────────│
  │── DISCONNECT ────────────────│                        │
  │── CONNECT (user=device_id) ──►│                        │
  │── SUBSCRIBE commands/update ►│                        │
  │── PUBLISH status (online) ──►│── forward ────────────►│
```

### Register payload (Agent → `devices/register`)

```json
{
  "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "hostname": "my-server-01",
  "plateforme": "linux",
  "os_version": "Ubuntu 22.04.3 LTS",
  "architecture": "x86_64",
  "version_agent": "1.0.0",
  "timestamp": "2025-01-15T10:30:00.000Z"
}
```

### Register ACK (Backend → `devices/{id}/register_ack`)

```json
// Success
{ "status": "ok", "device_id": "...", "token": "f9e8d7c6-...", "message": "Device registered successfully" }

// Error
{ "status": "error", "device_id": "...", "token": null, "message": "Registration failed: device revoked" }
```

### Heartbeat payload (Agent → `devices/{id}/status`)

```json
{
  "device_id": "a1b2c3d4-...",
  "timestamp": "2025-01-15T10:30:00.000Z",
  "status": "online",
  "version": "1.0.0",
  "uptime_sec": 3600,
  "hostname": "my-server-01",
  "plateforme": "linux",
  "cpu_percent": 12.5,
  "ram_percent": 45.2,
  "disk_percent": 67.0
}
```

Heartbeat interval: **30 seconds**. Device is marked `offline` after **5 minutes** of silence.

### Command result payload (Agent → `devices/{id}/results`)

```json
// Acknowledged
{ "command_id": "c1d2e3f4-...", "device_id": "...", "statut": "acknowledged", "output": null, "error": null, "exit_code": null, "timestamp": "..." }

// Success
{ "command_id": "c1d2e3f4-...", "device_id": "...", "statut": "success", "output": "nginx restarted.\n", "error": null, "exit_code": 0, "timestamp": "..." }

// Failed
{ "command_id": "c1d2e3f4-...", "device_id": "...", "statut": "failed", "output": null, "error": "Command exited with code 1", "exit_code": 1, "timestamp": "..." }
```

---

## WebSocket Events

Connect: `ws://localhost:8000/ws/connect?token=<JWT_ACCESS_TOKEN>`

All messages share the envelope format:
```json
{ "event": "<event_name>", "data": { ... }, "timestamp": "2025-01-15T10:30:00Z" }
```

| Event | Trigger | Key data fields |
|-------|---------|-----------------|
| `device_registered` | New agent onboards | `device_id`, `nom`, `plateforme`, `version`, `statut` |
| `device_status` | Heartbeat received | `device_id`, `statut`, `cpu_percent`, `ram_percent`, `derniere_connexion` |
| `new_log` | Log batch received | `id`, `device_id`, `level`, `source`, `message`, `metadata` |
| `command_update` | Command status changes | `command_id`, `statut`, `output`, `error`, `exit_code`, `termine_le` |
| `alert_triggered` | Alert condition met | `alert_id`, `alert_nom`, `device_id`, `condition`, `log` |
| `discovery_update` | Network scan received | `device_id`, `neighbors_count`, `network` |
| `agent_update_progress` | Agent update step | `command_id`, `device_id`, `statut`, `step` |

**Ping/Pong:** The dashboard should respond to server pings. If no pong received within 60s, the server closes with code `1001`.

### JavaScript client example

```javascript
const token = "eyJhbGci...";
const ws = new WebSocket(`ws://localhost:8000/ws/connect?token=${token}`);

ws.onopen = () => console.log("Connected to Orbis");

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  switch (msg.event) {
    case "device_status":
      updateDeviceCard(msg.data);
      break;
    case "new_log":
      appendLog(msg.data);
      break;
    case "command_update":
      updateCommandStatus(msg.data);
      break;
    case "alert_triggered":
      showAlert(msg.data);
      break;
  }
};

ws.onclose = (e) => {
  if (e.code === 4001) console.error("Invalid or expired token");
};
```

---

## Data Models

### Device

```
device_id         string    UUID v4 (generated by agent)
nom               string    Display name
hostname          string    OS hostname
plateforme        enum      windows | linux | macos | android
os_version        string    e.g. "Ubuntu 22.04.3 LTS"
architecture      enum      x86_64 | arm64 | armv7 | x86 | aarch64
statut            enum      online | offline | unknown | revoked
version_agent     string    semver e.g. "1.0.0"
derniere_connexion datetime  Last heartbeat timestamp
config_logs       object    { interval_sec, levels[], sources[] }
groupe_ids        string[]  Group MongoDB IDs containing this device
revoked           bool      True if device access has been revoked
created_at        datetime  Registration timestamp
metadata          object    Arbitrary key-value pairs
```

### Log

```
device_id   string    Source device UUID
timestamp   datetime  Event time (from agent clock)
level       enum      DEBUG | INFO | WARNING | ERROR | CRITICAL
source      string    Plugin name e.g. "disk_plugin" (max 100 chars)
message     string    Human-readable message (max 10 000 chars)
metadata    object    Structured data (e.g. { "percent": 97.0 })
received_at datetime  Server reception time (used for TTL index)
```

Log TTL: **90 days** (configurable via `LOG_RETENTION_DAYS`).

### Command

```
command_id    string    UUID v4 (generated by backend)
device_id     string    Target device UUID
type          string    shell | restart_service | collect_now | ...
payload       object    Type-specific parameters
timeout_sec   int       Execution timeout (default 30s)
statut        enum      pending | sent | acknowledged | executing | success | failed
resultat      string    stdout output (max 64 KB)
error_message string    Error description on failure
exit_code     int       Process exit code
cree_le       datetime  Command creation time
envoye_le     datetime  MQTT publish time
acquitte_le   datetime  Agent acknowledgement time
execute_le    datetime  Execution start time
termine_le    datetime  Completion time
cree_par      string    Operator user ID
```

---

## RBAC — Roles & Permissions

| Action | admin | operator | viewer |
|--------|-------|----------|--------|
| View devices, logs, commands | ✅ | ✅ | ✅ |
| View groups, actions, alerts | ✅ | ✅ | ✅ |
| View audit logs | ✅ | ✅ | ✅ |
| Send commands | ✅ | ✅ | ✗ |
| Create/update/delete resources | ✅ | ✅ | ✗ |
| Revoke devices | ✅ | ✅ | ✗ |
| Manage user accounts | ✅ | ✗ | ✗ |

---

## Testing

### Run all tests

```bash
# From backend/ directory, with venv activated
pytest tests/ -v

# With coverage report
pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=html
open htmlcov/index.html
```

### Run specific test files

```bash
pytest tests/test_devices.py -v      # Device endpoint tests
pytest tests/test_commands.py -v     # Command lifecycle tests
pytest tests/test_mqtt.py -v         # MQTT handler tests
```

### Test against a running instance

```bash
# Set token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@orbis.local","password":"admin_secret"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Health
curl http://localhost:8000/health

# List devices
curl http://localhost:8000/devices -H "Authorization: Bearer $TOKEN"

# Create a group
curl -X POST http://localhost:8000/groups \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"nom":"Test Group","description":"Integration test group"}'

# Send a command (replace DEVICE_ID with a real device UUID)
curl -X POST "http://localhost:8000/devices/DEVICE_ID/command" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type":"shell","payload":{"command":"uptime"},"timeout_sec":10}'

# Query audit logs
curl "http://localhost:8000/audit?limit=10" -H "Authorization: Bearer $TOKEN"

# Export audit CSV
curl "http://localhost:8000/audit/export" -H "Authorization: Bearer $TOKEN" -o audit.csv
```

### WebSocket test

```bash
# Install websocat (brew install websocat)
websocat "ws://localhost:8000/ws/connect?token=$TOKEN"

# Events will appear as JSON as devices connect and send data
```

---

## Docker Deployment

### Build and run

```bash
# From backend/ directory
docker build -t orbis-backend:latest .

docker run -d \
  --name orbis-backend \
  -p 8000:8000 \
  --env-file .env \
  orbis-backend:latest
```

### Environment variables for Docker

Pass all settings from `.env.example` as environment variables. The most critical ones:

```bash
docker run -d \
  -e MONGODB_URL=mongodb://mongo:27017 \
  -e MQTT_BROKER_HOST=emqx \
  -e MQTT_BROKER_PORT=8883 \
  -e MQTT_USE_TLS=true \
  -e MQTT_CA_CERT=/certs/ca.crt \
  -e JWT_SECRET_KEY=prod_secret_min_32_chars \
  -e ADMIN_PASSWORD=secure_admin_password \
  -v /path/to/certs:/certs:ro \
  orbis-backend:latest
```

### Health check

The Docker image includes a HEALTHCHECK:
```
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3
  CMD curl -f http://localhost:8000/health || exit 1
```

---

## Project Structure

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, lifespan, /health, /ws/connect
│   ├── config.py            # pydantic-settings — all env vars
│   ├── database.py          # Motor async, connect/close, MongoDB indexes
│   │
│   ├── models/              # Pydantic v2 document models
│   │   ├── base.py          # PyObjectId, OrbisBaseModel
│   │   ├── user.py          # User, UserPublic, UserCreate, UserUpdate
│   │   ├── device.py        # Device, DevicePublic, DeviceCreate, DeviceUpdate, LogConfig
│   │   ├── log.py           # Log, LogIncoming
│   │   ├── command.py       # Command, CommandCreate, CommandResult
│   │   ├── action.py        # Action, ActionCreate, ActionParameter, ActionExecuteRequest
│   │   ├── instruction.py   # Instruction, InstructionStep, InstructionCreate
│   │   ├── group.py         # Group, GroupCreate, GroupDevicesUpdate
│   │   ├── alert.py         # Alert, AlertCondition, AlertHistoryEntry
│   │   ├── audit.py         # AuditLog
│   │   └── agent_version.py # AgentVersion, AgentVersionCreate
│   │
│   ├── routers/             # One file per REST resource
│   │   ├── auth.py          # POST /auth/login|refresh|logout, GET /auth/me
│   │   ├── devices.py       # CRUD + /command + /update + /logs + /commands
│   │   ├── groups.py        # CRUD + /devices + /command
│   │   ├── commands.py      # GET /commands/{command_id}
│   │   ├── actions.py       # CRUD + /execute
│   │   ├── instructions.py  # CRUD + /execute
│   │   ├── alerts.py        # CRUD + /toggle
│   │   ├── agent_versions.py # CRUD + /set-current
│   │   ├── audit.py         # GET /audit + /audit/export
│   │   └── discovery.py     # GET /discovery/{device_id}
│   │
│   ├── services/            # Stateless business logic (no HTTP concerns)
│   │   ├── auth_service.py  # JWT create/decode, bcrypt, user CRUD
│   │   ├── device_service.py # Device CRUD, status update, inactivity check
│   │   ├── command_service.py # Command lifecycle, MQTT payload builder
│   │   ├── alert_service.py  # Alert condition evaluation, webhook
│   │   ├── emqx_service.py   # EMQX REST API (create/delete/kick MQTT users)
│   │   └── audit_service.py  # Audit log persistence and query
│   │
│   ├── mqtt/
│   │   ├── client.py        # aiomqtt loop, TLS context, exponential backoff
│   │   └── handlers.py      # handle_register, handle_logs, handle_status,
│   │                        #   handle_results, handle_discovery
│   │
│   ├── websocket/
│   │   └── manager.py       # WebSocketManager singleton (broadcast, send_to)
│   │
│   └── middleware/
│       ├── auth.py          # get_current_user, require_role factory, RBAC shortcuts
│       └── audit.py         # AuditMiddleware (Starlette BaseHTTPMiddleware)
│
├── tests/
│   ├── conftest.py          # Shared fixtures: mock DB, JWT tokens, sample payloads
│   ├── test_devices.py      # Device endpoint tests
│   ├── test_commands.py     # Command lifecycle tests
│   └── test_mqtt.py         # MQTT handler tests
│
├── Dockerfile               # Multi-stage build, non-root user, healthcheck
├── requirements.txt         # Pinned production + test dependencies
├── pytest.ini               # asyncio_mode = auto
└── .env.example             # Template for all environment variables
```

---

## Development Guide

### Adding a new endpoint

1. Add the Pydantic model to `app/models/` if needed
2. Add business logic to the appropriate service in `app/services/`
3. Add the FastAPI route to the appropriate router in `app/routers/`
4. Register the router in `app/main.py` (already included if using an existing router file)
5. Add audit mapping in `app/services/audit_service.py` if it's a mutation

### Adding a new MQTT topic

1. Add the subscription to `_SUBSCRIPTIONS` in `app/mqtt/client.py`
2. Add a handler function in `app/mqtt/handlers.py`
3. Add a dispatch case in `_dispatch_message()` in `app/mqtt/client.py`

### MongoDB index management

All indexes are created in `app/database.py::_setup_indexes()`. This runs on every startup and is idempotent (MongoDB ignores existing indexes with the same name).

### Coding conventions

- All async functions — no blocking I/O anywhere
- All external values from `settings` (never hardcoded)
- Type hints on every function signature
- `logger = logging.getLogger(__name__)` at the top of each module
- Pydantic v2 for all data validation — no unvalidated dicts crossing service boundaries
- `HTTPException` with a `code` header for all API errors

---

## Troubleshooting

### Backend won't start

```
RuntimeError: La connexion MongoDB n'est pas initialisée.
```
→ MongoDB is not running. Start it: `brew services start mongodb-community` (macOS) or `mongod`.

### MQTT keeps retrying

```
WARNING MQTT déconnecté : ... Retry dans 2.0s…
```
→ Normal without EMQX. The REST API works fully. Set `MQTT_USE_TLS=false` and ensure EMQX is running for full functionality. Check `MQTT_BROKER_HOST` and `MQTT_BROKER_PORT`.

### 401 Unauthorized on all requests

→ Check that `JWT_SECRET_KEY` in `.env` matches the key used to generate the token. Re-login to get a fresh token.

### 503 MQTT publish failed

```json
{ "detail": "MQTT publish failed", "code": "MQTT_PUBLISH_FAILED" }
```
→ The MQTT broker is unreachable at publish time. Check EMQX status and network connectivity.

### EMQX Admin API returns 401 BAD_API_KEY_OR_SECRET

```
ERROR Echec create_mqtt_user device=... : 401 {"code":"BAD_API_KEY_OR_SECRET",...}
```
→ The `EMQX_API_ID` / `EMQX_API_KEY` values in `.env` are wrong or missing.  
Go to **EMQX Dashboard → System → API Keys**, create a new key, and copy both the **AppID** and the **AppSecret** (the secret is only shown once). Set them in `.env`:

```env
EMQX_API_ID=<AppID shown in the list>
EMQX_API_KEY=<AppSecret shown at creation time>
```

Do **not** use the dashboard login password — EMQX 5.x requires a dedicated API key for the REST Admin API.

### Device stuck in `unknown` status

→ The device never sent a heartbeat. Check agent MQTT connectivity and that the agent is subscribed to the correct topics with valid credentials.

### Import error: `pydantic_core`

```
ImportError: cannot import name 'core_schema' from 'pydantic_core'
```
→ Pydantic version mismatch. Run: `pip install -r requirements.txt --force-reinstall`

### Tests fail with `RuntimeError: no running event loop`

→ Ensure `pytest.ini` contains `asyncio_mode = auto` and `pytest-asyncio` is installed.
