"""Service d'envoi d'emails d'alerte via SMTP (Brevo ou tout serveur compatible)."""
import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List

import aiosmtplib

from app.config import settings
from app.models.alert import Alert

logger = logging.getLogger(__name__)


# ─── Condition helpers ────────────────────────────────────────────────────────

_CONDITION_LABELS = {
    "log_level": "Log Level",
    "inactivity": "Inactivity",
    "metadata_threshold": "Metric Threshold",
}

_OPERATOR_LABELS = {
    "eq": "equals",
    "gt": "greater than",
    "lt": "less than",
    "gte": "greater or equal to",
    "lte": "less or equal to",
    "contains": "contains",
}

_LEVEL_COLORS = {
    "DEBUG":    "#64748b",
    "INFO":     "#3b82f6",
    "WARNING":  "#f59e0b",
    "ERROR":    "#ef4444",
    "CRITICAL": "#dc2626",
}

_CONDITION_TYPE_COLORS = {
    "log_level":          "#8b5cf6",
    "inactivity":         "#f59e0b",
    "metadata_threshold": "#06b6d4",
}


def _condition_summary(alert: Alert) -> str:
    cond = alert.condition
    op_label = _OPERATOR_LABELS.get(cond.operateur, cond.operateur)
    if cond.type == "log_level":
        return f"Log level {op_label} <strong>{cond.valeur}</strong>"
    if cond.type == "inactivity":
        return f"Device inactive for <strong>{cond.valeur} minutes</strong>"
    if cond.type == "metadata_threshold":
        return f"<code>{cond.metadata_key}</code> {op_label} <strong>{cond.valeur}</strong>"
    return str(cond.type)


# ─── HTML Template ────────────────────────────────────────────────────────────

def _build_html(
    alert: Alert,
    device_id: str,
    device_name: str,
    context: Dict[str, Any],
    triggered_at: datetime,
) -> str:
    """Génère un email HTML professionnel et responsive pour une alerte Orbis."""

    cond = alert.condition
    cond_label = _CONDITION_LABELS.get(cond.type, cond.type)
    cond_color = _CONDITION_TYPE_COLORS.get(cond.type, "#6366f1")
    cond_summary = _condition_summary(alert)

    log_level = str(context.get("log_level", ""))
    log_source = str(context.get("log_source", ""))
    log_message = str(context.get("log_message", ""))
    metadata: Dict[str, Any] = context.get("metadata", {}) or {}

    level_color = _LEVEL_COLORS.get(log_level.upper(), "#6366f1")

    ts = triggered_at.strftime("%Y-%m-%d at %H:%M:%S UTC")

    # Build metadata rows
    metadata_rows = ""
    if metadata:
        for key, val in list(metadata.items())[:8]:
            metadata_rows += f"""
                <tr>
                  <td style="padding:6px 12px;color:#64748b;font-size:12px;font-family:monospace;white-space:nowrap;">{key}</td>
                  <td style="padding:6px 12px;color:#0f172a;font-size:12px;font-family:monospace;">{val}</td>
                </tr>"""

    metadata_section = ""
    if metadata_rows:
        metadata_section = f"""
        <div style="margin-top:16px;">
          <p style="margin:0 0 8px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;">Metadata</p>
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0;border-collapse:collapse;overflow:hidden;">
            {metadata_rows}
          </table>
        </div>"""

    log_section = ""
    if log_message or log_source:
        level_badge = f"""<span style="display:inline-block;padding:2px 8px;background:{level_color}20;color:{level_color};border-radius:4px;font-size:11px;font-weight:700;">{log_level}</span>""" if log_level else ""
        source_badge = f"""<span style="margin-left:6px;font-size:11px;color:#64748b;font-family:monospace;">{log_source}</span>""" if log_source else ""
        log_section = f"""
        <div style="margin-top:16px;">
          <p style="margin:0 0 8px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;">Log Entry</p>
          <div style="background:#0f172a;border-radius:8px;padding:14px 16px;">
            <div style="margin-bottom:8px;">{level_badge}{source_badge}</div>
            <p style="margin:0;font-family:monospace;font-size:13px;color:#e2e8f0;line-height:1.5;word-break:break-all;">{log_message}</p>
          </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Orbis Alert — {alert.nom}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:32px 16px;">
    <tr>
      <td align="center">
        <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;">

          <!-- ── Header ──────────────────────────────────────────────── -->
          <tr>
            <td style="background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);border-radius:12px 12px 0 0;padding:28px 32px;">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td>
                    <p style="margin:0;font-size:20px;font-weight:800;color:#ffffff;letter-spacing:0.06em;">ORBIS</p>
                    <p style="margin:2px 0 0;font-size:12px;color:#94a3b8;letter-spacing:0.04em;">Platform Monitoring</p>
                  </td>
                  <td align="right">
                    <span style="display:inline-block;padding:5px 12px;background:rgba(239,68,68,0.2);border:1px solid rgba(239,68,68,0.4);border-radius:20px;font-size:11px;font-weight:700;color:#fca5a5;letter-spacing:0.06em;">
                      ⚠ ALERT TRIGGERED
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- ── Alert name banner ────────────────────────────────────── -->
          <tr>
            <td style="background:#ffffff;padding:24px 32px 20px;border-left:1px solid #e2e8f0;border-right:1px solid #e2e8f0;">
              <div style="display:flex;align-items:center;gap:10px;">
                <span style="display:inline-block;padding:4px 10px;background:{cond_color}18;border:1px solid {cond_color}40;border-radius:6px;font-size:11px;font-weight:700;color:{cond_color};letter-spacing:0.06em;text-transform:uppercase;">{cond_label}</span>
              </div>
              <h1 style="margin:10px 0 6px;font-size:22px;font-weight:800;color:#0f172a;line-height:1.2;">{alert.nom}</h1>
              <p style="margin:0;font-size:13px;color:#64748b;">Triggered on <strong style="color:#0f172a;">{ts}</strong></p>
            </td>
          </tr>

          <!-- ── Device info ──────────────────────────────────────────── -->
          <tr>
            <td style="background:#ffffff;padding:0 32px 24px;border-left:1px solid #e2e8f0;border-right:1px solid #e2e8f0;">
              <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:16px;">
                <tr>
                  <td width="50%" style="padding:0 8px 0 0;vertical-align:top;">
                    <p style="margin:0 0 3px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;">Device</p>
                    <p style="margin:0;font-size:14px;font-weight:700;color:#0f172a;">{device_name}</p>
                    <p style="margin:2px 0 0;font-family:monospace;font-size:11px;color:#64748b;">{device_id}</p>
                  </td>
                  <td width="50%" style="padding:0 0 0 8px;vertical-align:top;">
                    <p style="margin:0 0 3px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;">Condition</p>
                    <p style="margin:0;font-size:13px;color:#0f172a;">{cond_summary}</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- ── Context details ─────────────────────────────────────── -->
          <tr>
            <td style="background:#ffffff;padding:0 32px 28px;border-left:1px solid #e2e8f0;border-right:1px solid #e2e8f0;">
              {log_section}
              {metadata_section}
            </td>
          </tr>

          <!-- ── Divider ─────────────────────────────────────────────── -->
          <tr>
            <td style="background:#ffffff;padding:0 32px;border-left:1px solid #e2e8f0;border-right:1px solid #e2e8f0;">
              <div style="height:1px;background:#e2e8f0;"></div>
            </td>
          </tr>

          <!-- ── CTA ────────────────────────────────────────────────── -->
          <tr>
            <td style="background:#ffffff;padding:24px 32px 28px;border-left:1px solid #e2e8f0;border-right:1px solid #e2e8f0;">
              <p style="margin:0 0 16px;font-size:13px;color:#64748b;">Open the Orbis dashboard to inspect the device and take action.</p>
              <a href="#" style="display:inline-block;padding:11px 24px;background:linear-gradient(135deg,#6366f1,#4f46e5);border-radius:8px;font-size:13px;font-weight:700;color:#ffffff;text-decoration:none;letter-spacing:0.02em;">
                View in Dashboard →
              </a>
            </td>
          </tr>

          <!-- ── Footer ─────────────────────────────────────────────── -->
          <tr>
            <td style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:0 0 12px 12px;padding:20px 32px;">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td>
                    <p style="margin:0;font-size:11px;color:#94a3b8;">
                      This is an automated notification from <strong style="color:#64748b;">Orbis Platform</strong>.
                      Alert ID: <span style="font-family:monospace;">{str(alert.id)[:8]}…</span>
                    </p>
                    <p style="margin:6px 0 0;font-size:11px;color:#cbd5e1;">
                      You are receiving this because you are listed as a recipient for this alert.
                      To stop receiving emails, edit the alert in the Orbis dashboard.
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _build_plain(
    alert: Alert,
    device_id: str,
    device_name: str,
    context: Dict[str, Any],
    triggered_at: datetime,
) -> str:
    """Version texte brut de l'email (fallback)."""
    ts = triggered_at.strftime("%Y-%m-%d at %H:%M:%S UTC")
    cond = alert.condition
    op_label = _OPERATOR_LABELS.get(cond.operateur, cond.operateur)

    if cond.type == "log_level":
        cond_desc = f"Log level {op_label} {cond.valeur}"
    elif cond.type == "inactivity":
        cond_desc = f"Device inactive for {cond.valeur} minutes"
    else:
        cond_desc = f"{cond.metadata_key} {op_label} {cond.valeur}"

    lines = [
        "ORBIS PLATFORM — ALERT TRIGGERED",
        "=" * 48,
        f"Alert : {alert.nom}",
        f"Time  : {ts}",
        "",
        "DEVICE",
        f"  Name : {device_name}",
        f"  ID   : {device_id}",
        "",
        "CONDITION",
        f"  {cond_desc}",
        "",
    ]

    if context.get("log_level"):
        lines += [
            "LOG ENTRY",
            f"  Level   : {context.get('log_level')}",
            f"  Source  : {context.get('log_source')}",
            f"  Message : {context.get('log_message')}",
            "",
        ]

    metadata = context.get("metadata", {})
    if metadata:
        lines.append("METADATA")
        for k, v in list(metadata.items())[:8]:
            lines.append(f"  {k} = {v}")
        lines.append("")

    lines += [
        "─" * 48,
        "Orbis Platform — automated alert notification",
    ]
    return "\n".join(lines)


# ─── SMTP connectivity check ─────────────────────────────────────────────────

async def check_smtp_config() -> tuple[bool, str]:
    """
    Vérifie la configuration SMTP en ouvrant une vraie connexion (EHLO + STARTTLS + AUTH).
    Retourne (success: bool, detail_message: str).
    Aucun email n'est envoyé — la connexion est fermée proprement après vérification.
    """
    if not settings.SMTP_ENABLED:
        return False, "SMTP is disabled in configuration (SMTP_ENABLED=false)"

    if not settings.SMTP_HOST:
        return False, "SMTP host is not configured (SMTP_HOST is empty)"

    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        return False, "SMTP credentials are incomplete (SMTP_USERNAME or SMTP_PASSWORD not set)"

    if not settings.SMTP_FROM_EMAIL:
        return False, "Sender address is not configured (SMTP_FROM_EMAIL is empty)"

    try:
        # Mirror exactly what aiosmtplib.send() does internally with start_tls=True:
        # open a plain connection, let the library handle STARTTLS automatically,
        # then authenticate — without delivering any message.
        async with aiosmtplib.SMTP(
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            start_tls=True,
            timeout=10,
        ) as smtp:
            await smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)

        return True, (
            f"SMTP relay reachable and authenticated — "
            f"{settings.SMTP_HOST}:{settings.SMTP_PORT} · sender: {settings.SMTP_FROM_EMAIL}"
        )
    except aiosmtplib.SMTPAuthenticationError as exc:
        return False, f"SMTP authentication failed for user '{settings.SMTP_USERNAME}': {exc}"
    except aiosmtplib.SMTPConnectError as exc:
        return False, f"Cannot connect to SMTP relay {settings.SMTP_HOST}:{settings.SMTP_PORT}: {exc}"
    except Exception as exc:
        return False, f"SMTP check error ({settings.SMTP_HOST}:{settings.SMTP_PORT}): {exc}"


# ─── Send function ────────────────────────────────────────────────────────────

async def send_alert_email(
    alert: Alert,
    device_id: str,
    device_name: str,
    context: Dict[str, Any],
    recipients: List[str],
) -> None:
    """
    Envoie un email d'alerte aux destinataires listés.
    Ne fait rien si SMTP_ENABLED=false ou si la liste est vide.
    """
    if not settings.SMTP_ENABLED:
        logger.debug("Email service disabled (SMTP_ENABLED=false) — skipping alert email")
        return

    if not recipients:
        return

    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        logger.warning("SMTP credentials not configured — skipping alert email for '%s'", alert.nom)
        return

    now = datetime.now(timezone.utc)
    subject = f"[Orbis Alert] {alert.nom} — {alert.condition.type.replace('_', ' ').title()}"

    html_body = _build_html(alert, device_id, device_name, context, now)
    plain_body = _build_plain(alert, device_id, device_name, context, now)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USERNAME,
            password=settings.SMTP_PASSWORD,
            start_tls=True,
        )
        logger.info(
            "Alert email sent for '%s' → %d recipient(s): %s",
            alert.nom,
            len(recipients),
            ", ".join(recipients),
        )
    except Exception as exc:
        logger.error("Failed to send alert email for '%s': %s", alert.nom, exc)
