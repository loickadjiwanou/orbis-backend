# ================================================================
# Orbis Backend — Dockerfile multi-stage
# ================================================================

# --- Stage 1 : installation des dépendances ---
FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Stage 2 : image finale allégée ---
FROM python:3.11-slim

WORKDIR /app

# Copie des packages installés depuis le builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copie du code source
COPY . .

# Utilisateur non-root pour la sécurité
RUN useradd -m -u 1000 orbis && chown -R orbis:orbis /app
USER orbis

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
