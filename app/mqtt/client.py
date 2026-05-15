"""Client MQTT async (aiomqtt) avec reconnexion automatique et TLS."""
import asyncio
import json
import logging
import ssl
from typing import Optional

import aiomqtt

from app.config import settings
from app.mqtt import handlers

logger = logging.getLogger(__name__)

# Topics auxquels le backend s'abonne
_SUBSCRIPTIONS = [
    ("devices/register", 1),
    ("devices/+/logs", 1),
    ("devices/+/status", 1),
    ("devices/+/results", 1),
    ("devices/+/discovery", 1),
    ("devices/+/agent_logs", 0),
    ("devices/+/update_progress", 1),
]

_mqtt_client_running = False
_mqtt_connected = False


def is_connected() -> bool:
    """Indique si le client MQTT est actuellement connecté au broker."""
    return _mqtt_connected


async def _build_tls_context() -> Optional[ssl.SSLContext]:
    """Construit le contexte TLS si activé."""
    if not settings.MQTT_USE_TLS:
        return None
    ctx = ssl.create_default_context()
    if settings.MQTT_CA_CERT:
        ctx.load_verify_locations(settings.MQTT_CA_CERT)
    else:
        # En dev, on accepte les certificats auto-signés
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _dispatch_message(topic: str, payload: bytes) -> None:
    """Dispatche un message MQTT vers le handler approprié."""
    parts = topic.split("/")
    try:
        if topic == "devices/register":
            data = json.loads(payload)
            await handlers.handle_register(data)

        elif len(parts) == 3 and parts[2] == "logs":
            device_id = parts[1]
            data = json.loads(payload)
            await handlers.handle_logs(device_id, data)

        elif len(parts) == 3 and parts[2] == "status":
            device_id = parts[1]
            data = json.loads(payload)
            await handlers.handle_status(device_id, data)

        elif len(parts) == 3 and parts[2] == "results":
            device_id = parts[1]
            data = json.loads(payload)
            await handlers.handle_results(device_id, data)

        elif len(parts) == 3 and parts[2] == "discovery":
            device_id = parts[1]
            data = json.loads(payload)
            await handlers.handle_discovery(device_id, data)

        elif len(parts) == 3 and parts[2] == "agent_logs":
            device_id = parts[1]
            data = json.loads(payload)
            await handlers.handle_agent_logs(device_id, data)

        elif len(parts) == 3 and parts[2] == "update_progress":
            device_id = parts[1]
            data = json.loads(payload)
            await handlers.handle_update_progress(device_id, data)

    except json.JSONDecodeError as exc:
        logger.warning("Payload JSON invalide sur topic=%s : %s", topic, exc)
    except Exception as exc:
        logger.error("Erreur handler topic=%s : %s", topic, exc, exc_info=True)


async def run_mqtt_client() -> None:
    """
    Boucle principale du client MQTT avec reconnexion exponentielle.
    Doit être lancée en tâche asyncio dans le lifespan de l'application.
    """
    global _mqtt_client_running, _mqtt_connected
    _mqtt_client_running = True
    delay = 1.0

    tls_context = await _build_tls_context()

    while _mqtt_client_running:
        try:
            logger.info(
                "Connexion MQTT → %s:%d (TLS=%s)",
                settings.MQTT_BROKER_HOST,
                settings.MQTT_BROKER_PORT,
                settings.MQTT_USE_TLS,
            )
            async with aiomqtt.Client(
                hostname=settings.MQTT_BROKER_HOST,
                port=settings.MQTT_BROKER_PORT,
                username=settings.MQTT_USERNAME,
                password=settings.MQTT_PASSWORD,
                tls_context=tls_context,
                keepalive=60,               # Session 0, Section 14
                client_id="orbis-backend",
            ) as client:
                _mqtt_connected = True
                delay = 1.0                 # Reset backoff après connexion réussie
                logger.info("MQTT connecté. Abonnements en cours...")

                async with client.messages() as messages:
                    for topic, qos in _SUBSCRIPTIONS:
                        await client.subscribe(topic, qos=qos)
                        logger.debug("Abonné : %s (QoS=%d)", topic, qos)

                    logger.info("MQTT prêt — %d topics actifs.", len(_SUBSCRIPTIONS))

                    async for message in messages:
                        if not _mqtt_client_running:
                            break
                        topic = str(message.topic)
                        payload = message.payload if isinstance(message.payload, bytes) else message.payload.encode()
                        asyncio.create_task(_dispatch_message(topic, payload))

        except aiomqtt.MqttError as exc:
            _mqtt_connected = False
            logger.warning("MQTT déconnecté : %s. Retry dans %.1fs…", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)   # Backoff exponentiel max 60s (Session 0, Section 14)

        except asyncio.CancelledError:
            logger.info("MQTT client arrêté.")
            break

        except Exception as exc:
            _mqtt_connected = False
            logger.error("Erreur inattendue MQTT : %s", exc, exc_info=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)

    _mqtt_connected = False
    _mqtt_client_running = False
    logger.info("Boucle MQTT terminée.")


async def stop_mqtt_client() -> None:
    """Demande l'arrêt propre du client MQTT."""
    global _mqtt_client_running
    _mqtt_client_running = False


async def publish(topic: str, payload: str, qos: int = 1, retain: bool = False) -> None:
    """
    Publie un message MQTT depuis le backend.
    Crée une connexion éphémère pour ne pas interférer avec la boucle de réception.
    """
    tls_context = await _build_tls_context()
    try:
        async with aiomqtt.Client(
            hostname=settings.MQTT_BROKER_HOST,
            port=settings.MQTT_BROKER_PORT,
            username=settings.MQTT_USERNAME,
            password=settings.MQTT_PASSWORD,
            tls_context=tls_context,
            keepalive=60,
            client_id="orbis-backend-pub",
        ) as client:
            await client.publish(topic, payload=payload.encode(), qos=qos, retain=retain)
            logger.debug("MQTT publié : topic=%s qos=%d retain=%s", topic, qos, retain)
    except aiomqtt.MqttError as exc:
        logger.error("Echec publication MQTT topic=%s : %s", topic, exc)
        raise
