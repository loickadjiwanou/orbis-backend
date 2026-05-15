"""Service d'interaction avec l'API REST Admin EMQX 5.x."""
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_AUTHN_PATH = "/api/v5/authentication/password_based:built_in_database/users"
_CLIENTS_PATH = "/api/v5/clients"


class EmqxService:
    """Wrapper async autour de l'API REST EMQX pour gérer les comptes MQTT."""

    def __init__(self, api_url: str, api_id: str, api_key: str) -> None:
        self._base_url = api_url.rstrip("/")
        # EMQX API v5 : Basic Auth avec AppID / AppSecret (System → API Keys)
        self._auth = httpx.BasicAuth(api_id, api_key)

    # ------------------------------------------------------------------
    # Comptes MQTT (authentification built-in database EMQX)
    # ------------------------------------------------------------------

    async def create_mqtt_user(self, device_id: str, password: str) -> bool:
        """
        Crée un compte MQTT pour un device lors de l'onboarding.

        POST /api/v5/authentication/password_based:built_in_database/users
        Retourne True si créé (201) ou déjà existant (409).
        """
        url = f"{self._base_url}{_AUTHN_PATH}"
        body = {
            "user_id": device_id,
            "password": password,
            "is_superuser": False,
        }
        async with httpx.AsyncClient(auth=self._auth, timeout=10.0) as client:
            try:
                resp = await client.post(url, json=body)
                if resp.status_code in (201, 409):
                    logger.info("Compte MQTT créé/existant pour device=%s", device_id)
                    return True
                logger.error(
                    "Echec create_mqtt_user device=%s : %s %s",
                    device_id,
                    resp.status_code,
                    resp.text,
                )
                return False
            except httpx.RequestError as exc:
                logger.error("Erreur HTTP EMQX create_mqtt_user : %s", exc)
                return False

    async def delete_mqtt_user(self, device_id: str) -> bool:
        """
        Supprime le compte MQTT d'un device révoqué.

        DELETE /api/v5/authentication/password_based:built_in_database/users/{device_id}
        """
        url = f"{self._base_url}{_AUTHN_PATH}/{device_id}"
        async with httpx.AsyncClient(auth=self._auth, timeout=10.0) as client:
            try:
                resp = await client.delete(url)
                if resp.status_code in (204, 404):
                    logger.info("Compte MQTT supprimé pour device=%s", device_id)
                    return True
                logger.error(
                    "Echec delete_mqtt_user device=%s : %s %s",
                    device_id,
                    resp.status_code,
                    resp.text,
                )
                return False
            except httpx.RequestError as exc:
                logger.error("Erreur HTTP EMQX delete_mqtt_user : %s", exc)
                return False

    # ------------------------------------------------------------------
    # Clients connectés
    # ------------------------------------------------------------------

    async def kick_client(self, device_id: str) -> bool:
        """
        Force la déconnexion immédiate d'un client MQTT connecté.

        DELETE /api/v5/clients/{device_id}
        """
        url = f"{self._base_url}{_CLIENTS_PATH}/{device_id}"
        async with httpx.AsyncClient(auth=self._auth, timeout=10.0) as client:
            try:
                resp = await client.delete(url)
                if resp.status_code in (204, 404):
                    logger.info("Client MQTT kické : device=%s", device_id)
                    return True
                logger.error(
                    "Echec kick_client device=%s : %s %s",
                    device_id,
                    resp.status_code,
                    resp.text,
                )
                return False
            except httpx.RequestError as exc:
                logger.error("Erreur HTTP EMQX kick_client : %s", exc)
                return False

    async def get_client_info(self, device_id: str) -> Optional[dict]:
        """
        Récupère les infos d'un client MQTT (IP, version, connecté depuis…).

        GET /api/v5/clients/{device_id}
        """
        url = f"{self._base_url}{_CLIENTS_PATH}/{device_id}"
        async with httpx.AsyncClient(auth=self._auth, timeout=10.0) as client:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
                return None
            except httpx.RequestError as exc:
                logger.error("Erreur HTTP EMQX get_client_info : %s", exc)
                return None

    async def list_connected_clients(self) -> list:
        """
        Liste tous les clients actuellement connectés au broker.

        GET /api/v5/clients
        """
        url = f"{self._base_url}{_CLIENTS_PATH}"
        async with httpx.AsyncClient(auth=self._auth, timeout=10.0) as client:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json().get("data", [])
                return []
            except httpx.RequestError as exc:
                logger.error("Erreur HTTP EMQX list_connected_clients : %s", exc)
                return []


# Instance singleton utilisée dans toute l'application
emqx_service = EmqxService(
    api_url=settings.EMQX_API_URL,
    api_id=settings.EMQX_API_ID,
    api_key=settings.EMQX_API_KEY,
)
