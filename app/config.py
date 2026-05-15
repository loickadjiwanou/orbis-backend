import sys
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import List, Optional

# Valeurs par défaut interdites en production (détectées au démarrage)
_DEFAULT_JWT_SECRET = "change_me_in_production_use_a_long_random_string"
_DEFAULT_ADMIN_PASSWORD = "admin_secret"
_DEFAULT_MQTT_REGISTER_SECRET = "orbis_register_secret"


class Settings(BaseSettings):
    # MongoDB
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DB: str = "orbis"

    # MQTT (broker EMQX)
    MQTT_BROKER_HOST: str = "localhost"
    MQTT_BROKER_PORT: int = 8883
    MQTT_USERNAME: str = "backend"
    MQTT_PASSWORD: str = ""
    MQTT_USE_TLS: bool = True
    MQTT_CA_CERT: Optional[str] = None

    # EMQX Admin API (pour créer/supprimer les comptes MQTT des devices)
    EMQX_API_URL: str = "http://emqx:18083"
    EMQX_API_ID:  str = ""  # AppID généré dans EMQX → System → API Keys
    EMQX_API_KEY: str = ""  # Secret Key correspondante

    # JWT
    JWT_SECRET_KEY: str = _DEFAULT_JWT_SECRET
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_HOURS: int = 24
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Rétention des logs (TTL MongoDB)
    LOG_RETENTION_DAYS: int = 90

    # Admin par défaut (créé au premier démarrage via /auth/setup)
    ADMIN_EMAIL: str = "admin@orbis.local"
    ADMIN_PASSWORD: str = _DEFAULT_ADMIN_PASSWORD
    ADMIN_FULL_NAME: str = "Administrator"

    # Secret partagé avec les agents pour le premier onboarding
    MQTT_REGISTER_SECRET: str = _DEFAULT_MQTT_REGISTER_SECRET

    # Version de l'application
    APP_VERSION: str = "1.0.0"

    # ── SMTP / Brevo ─────────────────────────────────────────────────────────
    SMTP_HOST: str = "smtp-relay.brevo.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""          # Votre email Brevo ou identifiant SMTP
    SMTP_PASSWORD: str = ""          # Clé SMTP générée dans Brevo → SMTP & API
    SMTP_FROM_EMAIL: str = "noreply@orbis-platform.io"
    SMTP_FROM_NAME: str = "Orbis Platform"
    SMTP_ENABLED: bool = False       # Mettre à True une fois les credentials configurés

    # ── Mode production ───────────────────────────────────────────────────────
    # Mettre à True pour activer la validation stricte des secrets (recommandé en prod)
    PRODUCTION: bool = False

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError(
                "JWT_SECRET_KEY doit faire au moins 32 caractères. "
                "Génère-en un avec : python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def check_production_secrets(self) -> None:
        """
        Appelé au démarrage. Lève une SystemExit si PRODUCTION=true
        et que des secrets par défaut sont détectés.
        En dev (PRODUCTION=false), affiche juste un avertissement.
        """
        if not self.PRODUCTION:
            # Mode dev — avertissements non bloquants
            warnings: list[str] = []
            if self.JWT_SECRET_KEY == _DEFAULT_JWT_SECRET:
                warnings.append("JWT_SECRET_KEY")
            if self.ADMIN_PASSWORD == _DEFAULT_ADMIN_PASSWORD:
                warnings.append("ADMIN_PASSWORD")
            if self.MQTT_REGISTER_SECRET == _DEFAULT_MQTT_REGISTER_SECRET:
                warnings.append("MQTT_REGISTER_SECRET")
            if warnings:
                import logging
                logging.getLogger(__name__).warning(
                    "⚠️  Secrets par défaut détectés (%s). "
                    "Définis PRODUCTION=true dans .env pour bloquer le démarrage en production.",
                    ", ".join(warnings),
                )
            return

        errors: list[str] = []

        if self.JWT_SECRET_KEY == _DEFAULT_JWT_SECRET:
            errors.append(
                "JWT_SECRET_KEY est la valeur par défaut. "
                "Génère-en un : python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if self.ADMIN_PASSWORD == _DEFAULT_ADMIN_PASSWORD:
            errors.append(
                "ADMIN_PASSWORD est 'admin_secret'. "
                "Définis un mot de passe fort dans .env (ADMIN_PASSWORD=...)."
            )
        if self.MQTT_REGISTER_SECRET == _DEFAULT_MQTT_REGISTER_SECRET:
            errors.append(
                "MQTT_REGISTER_SECRET est la valeur par défaut. "
                "Change-la dans .env pour éviter les enregistrements non autorisés."
            )

        if errors:
            print("\n" + "=" * 70)
            print("❌  ORBIS BACKEND — ERREUR DE CONFIGURATION PRODUCTION")
            print("=" * 70)
            for i, err in enumerate(errors, 1):
                print(f"  [{i}] {err}")
            print("=" * 70)
            print("Corrige ces valeurs dans ton fichier .env et redémarre.\n")
            sys.exit(1)


settings = Settings()
