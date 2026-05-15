"""Gestionnaire de connexions WebSocket vers le dashboard."""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Gère toutes les connexions WebSocket actives.
    Thread-safe via asyncio.Lock pour les opérations sur le dict de connexions.
    """

    def __init__(self) -> None:
        self._connections: Dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, client_id: str, websocket: WebSocket) -> None:
        """Accepte et enregistre une connexion WebSocket."""
        await websocket.accept()
        async with self._lock:
            self._connections[client_id] = websocket
        logger.info("WebSocket connecté : client_id=%s (total=%d)", client_id, len(self._connections))

    async def disconnect(self, client_id: str) -> None:
        """Supprime une connexion WebSocket du registre."""
        async with self._lock:
            self._connections.pop(client_id, None)
        logger.info("WebSocket déconnecté : client_id=%s (total=%d)", client_id, len(self._connections))

    async def send_to(self, client_id: str, event: str, data: dict) -> None:
        """Envoie un message à un client spécifique."""
        websocket = self._connections.get(client_id)
        if websocket is None:
            return
        message = self._build_message(event, data)
        try:
            await websocket.send_text(message)
        except Exception as exc:
            logger.warning("Erreur envoi WebSocket client_id=%s : %s", client_id, exc)
            await self.disconnect(client_id)

    async def broadcast(self, event: str, data: dict) -> None:
        """
        Diffuse un événement à TOUS les clients connectés.
        Format : { "event": "...", "data": {...}, "timestamp": "ISO8601" }
        """
        if not self._connections:
            return
        message = self._build_message(event, data)
        dead: list[str] = []
        async with self._lock:
            clients = list(self._connections.items())
        for client_id, websocket in clients:
            try:
                await websocket.send_text(message)
            except Exception as exc:
                logger.warning("Broadcast échoué client_id=%s : %s", client_id, exc)
                dead.append(client_id)
        for client_id in dead:
            await self.disconnect(client_id)

    @staticmethod
    def _build_message(event: str, data: dict) -> str:
        return json.dumps(
            {
                "event": event,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
            default=str,
        )

    @property
    def connected_count(self) -> int:
        return len(self._connections)


# Instance singleton partagée dans toute l'application
ws_manager = WebSocketManager()
