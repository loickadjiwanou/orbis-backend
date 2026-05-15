"""Endpoints d'authentification JWT."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User, UserPublic, UserRole
from app.services.auth_service import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    create_user,
    decode_token,
    get_user_by_id,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Obtain JWT tokens",
    description=(
        "Authenticates a user with email and password. "
        "Returns an **access token** (24 h) and a **refresh token** (7 days). "
        "Pass the access token as `Authorization: Bearer <token>` on all protected endpoints. "
        "**Rate limited to 10 attempts per minute per IP.**"
    ),
    responses={
        200: {"description": "Authentication successful — tokens returned"},
        401: {"description": "Invalid credentials", "content": {"application/json": {"example": {"detail": "Invalid credentials", "code": "UNAUTHORIZED"}}}},
        429: {"description": "Too many requests — rate limit exceeded"},
    },
)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, db: AsyncIOMotorDatabase = Depends(get_db)) -> TokenResponse:
    """Authenticate and receive JWT tokens."""
    from app.config import settings

    user = await authenticate_user(db, body.email, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"code": "UNAUTHORIZED"},
        )
    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        user=UserPublic(**user.model_dump(by_alias=True)),
    )


@router.post("/refresh")
async def refresh_token(
    body: RefreshRequest, db: AsyncIOMotorDatabase = Depends(get_db)
) -> dict:
    """Génère un nouvel access token depuis un refresh token valide."""
    from app.config import settings
    from jose import JWTError

    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise JWTError("Type de token invalide")
        user_id = payload.get("sub")
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalide ou expiré",
        ) from exc

    user = await get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Utilisateur invalide")

    access_token = create_access_token(user)
    return {
        "access_token": access_token,
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_HOURS * 3600,
    }


class SetupRequest(BaseModel):
    email: str
    password: str
    full_name: str


@router.get(
    "/setup",
    summary="Check if initial setup is required",
    description="Returns whether the platform has no admin account yet. No authentication required.",
)
async def check_setup(db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    """Vérifie si un admin existe déjà en base."""
    admin_count = await db.users.count_documents({"role": UserRole.admin})
    return {"setup_required": admin_count == 0}


@router.post(
    "/setup",
    response_model=TokenResponse,
    summary="Create the first admin account",
    description=(
        "Creates the initial administrator account. "
        "**Blocked with 409 if any admin already exists.** No authentication required. "
        "**Rate limited to 5 attempts per minute per IP.**"
    ),
    responses={
        409: {"description": "Setup already completed or email already exists"},
        429: {"description": "Too many requests — rate limit exceeded"},
    },
)
@limiter.limit("5/minute")
async def initial_setup(
    request: Request, body: SetupRequest, db: AsyncIOMotorDatabase = Depends(get_db)
) -> TokenResponse:
    """Crée le premier compte admin. Échoue si un admin existe déjà."""
    admin_count = await db.users.count_documents({"role": UserRole.admin})
    if admin_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup already completed — an admin account already exists.",
        )
    existing = await db.users.find_one({"email": body.email})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    user = await create_user(
        db,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=UserRole.admin,
    )
    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)
    from app.config import settings
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        user=UserPublic(**user.model_dump(by_alias=True)),
    )


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str


@router.post(
    "/register",
    response_model=TokenResponse,
    summary="Create a new account",
    description=(
        "Creates a new user account with the **admin** role. "
        "Returns JWT tokens immediately — the user is logged in right after registration. "
        "**Rate limited to 5 attempts per minute per IP.**"
    ),
    responses={
        409: {"description": "An account with this email already exists"},
        429: {"description": "Too many requests — rate limit exceeded"},
    },
)
@limiter.limit("5/minute")
async def register(
    request: Request, body: RegisterRequest, db: AsyncIOMotorDatabase = Depends(get_db)
) -> TokenResponse:
    """Crée un nouveau compte admin et retourne les tokens JWT."""
    existing = await db.users.find_one({"email": body.email})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
            headers={"code": "EMAIL_TAKEN"},
        )
    user = await create_user(
        db,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=UserRole.admin,
    )
    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)
    from app.config import settings
    logger.info("New account registered: %s (admin)", user.email)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        user=UserPublic(**user.model_dump(by_alias=True)),
    )


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)) -> dict:
    """Déconnexion (côté serveur stateless — le client doit supprimer le token)."""
    logger.info("Logout : %s", current_user.email)
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserPublic)
async def get_me(current_user: User = Depends(get_current_user)) -> UserPublic:
    """Retourne le profil de l'utilisateur connecté."""
    return UserPublic(**current_user.model_dump(by_alias=True))
