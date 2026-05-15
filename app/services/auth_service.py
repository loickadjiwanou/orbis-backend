"""Service d'authentification : JWT, hachage de mots de passe, RBAC."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from jose import JWTError, jwt
from passlib.context import CryptContext
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Hachage de mots de passe
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Retourne le hash bcrypt du mot de passe."""
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Vérifie qu'un mot de passe correspond à son hash bcrypt."""
    return _pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# Tokens JWT
# ---------------------------------------------------------------------------

def _create_token(data: dict, expire_delta: timedelta) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + expire_delta
    payload["iat"] = datetime.now(timezone.utc)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user: User) -> str:
    """Crée un access token JWT valable ACCESS_TOKEN_EXPIRE_HOURS heures."""
    return _create_token(
        {"sub": str(user.id), "email": user.email, "role": user.role},
        timedelta(hours=settings.ACCESS_TOKEN_EXPIRE_HOURS),
    )


def create_refresh_token(user: User) -> str:
    """Crée un refresh token JWT valable REFRESH_TOKEN_EXPIRE_DAYS jours."""
    return _create_token(
        {"sub": str(user.id), "type": "refresh"},
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str) -> dict:
    """Décode un JWT et retourne le payload. Lève JWTError si invalide."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


# ---------------------------------------------------------------------------
# Opérations MongoDB sur les utilisateurs
# ---------------------------------------------------------------------------

async def get_user_by_email(db: AsyncIOMotorDatabase, email: str) -> Optional[User]:
    """Récupère un User par email depuis MongoDB."""
    doc = await db.users.find_one({"email": email})
    if doc is None:
        return None
    return User(**doc)


async def get_user_by_id(db: AsyncIOMotorDatabase, user_id: str) -> Optional[User]:
    """Récupère un User par son ObjectId depuis MongoDB."""
    try:
        oid = ObjectId(user_id)
    except Exception:
        return None
    doc = await db.users.find_one({"_id": oid})
    if doc is None:
        return None
    return User(**doc)


async def authenticate_user(
    db: AsyncIOMotorDatabase, email: str, password: str
) -> Optional[User]:
    """Authentifie un utilisateur. Retourne User ou None si invalide."""
    user = await get_user_by_email(db, email)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    # Mise à jour last_login
    await db.users.update_one(
        {"_id": ObjectId(str(user.id))},
        {"$set": {"last_login": datetime.now(timezone.utc)}},
    )
    return user


async def create_user(
    db: AsyncIOMotorDatabase,
    email: str,
    password: str,
    full_name: str,
    role: UserRole,
) -> User:
    """Crée un nouvel utilisateur en MongoDB."""
    now = datetime.now(timezone.utc)
    doc = {
        "email": email,
        "hashed_password": hash_password(password),
        "full_name": full_name,
        "role": role,
        "is_active": True,
        "created_at": now,
        "last_login": None,
    }
    result = await db.users.insert_one(doc)
    doc["_id"] = result.inserted_id
    logger.info("Utilisateur créé : %s (%s)", email, role)
    return User(**doc)


async def ensure_admin_exists(db: AsyncIOMotorDatabase) -> None:
    """Crée l'admin par défaut si aucun utilisateur admin n'existe."""
    existing = await db.users.find_one({"email": settings.ADMIN_EMAIL})
    if existing is None:
        await create_user(
            db,
            email=settings.ADMIN_EMAIL,
            password=settings.ADMIN_PASSWORD,
            full_name=settings.ADMIN_FULL_NAME,
            role=UserRole.admin,
        )
        logger.info("Admin par défaut créé : %s", settings.ADMIN_EMAIL)
