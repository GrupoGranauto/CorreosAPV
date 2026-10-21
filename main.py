import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

import worker
from mailer import GmailSender, build_invite

# ----------------------------------------------------
# 1. LOGGING ESTRUCTURADO
# ----------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("apv_calendars")

# ----------------------------------------------------
# 2. CONFIGURACIÓN (worker.py ya cargó .env si existe)
# ----------------------------------------------------
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/Mexico_City")
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "").strip()
WORKER_ENABLED = os.getenv("WORKER_ENABLED", "true").lower() == "true"

if API_SECRET_KEY:
    logger.info("Autenticación por API key ACTIVADA.")
else:
    logger.warning("API_SECRET_KEY no configurada — el endpoint /webhook está ABIERTO sin autenticación.")

gmail_sender = GmailSender.from_env()
if not gmail_sender:
    logger.warning("Gmail API no configurada — /webhook funcionará en modo simulación.")


# ----------------------------------------------------
# 3. CICLO DE VIDA: ARRANCAR EL WORKER DE SHEETS
# ----------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = None
    if WORKER_ENABLED:
        stop_event = worker.start_in_background()
    else:
        worker.status["disabled_reason"] = "WORKER_ENABLED=false"
    yield
    if stop_event:
        stop_event.set()


# ----------------------------------------------------
# 4. DEFINICIÓN DEL PAYLOAD DE ENTRADA (WEBHOOK)
# ----------------------------------------------------
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class WebhookPayload(BaseModel):
    apv: str
    nombre: str
    apellido: str
    contacto_cliente: str = Field(alias="contacto_cliente")
    unidad_de_interes: str = Field(alias="unidad_de_interes")
    agencia_de_cita: str = Field(alias="agencia_de_cita")
    bdc_responsable: str = Field(alias="bdc_responsable")
    dia_de_cita: str = Field(alias="dia_de_cita")
    hora_de_cita: str = Field(alias="hora_de_cita")
    email_apv: str = Field(alias="email_apv")
    timezone: str = "America/Mexico_City"

    class Config:
        populate_by_name = True

    @field_validator("email_apv")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        v = v.strip()
        if not EMAIL_REGEX.match(v):
            raise ValueError(f"El email del APV no tiene un formato válido: '{v}'")
        return v


# ----------------------------------------------------
# 5. DEPENDENCIA DE AUTENTICACIÓN POR API KEY
# ----------------------------------------------------
async def verify_api_key(x_api_key: Optional[str] = Header(default=None)):
    """Valida la API key enviada en la cabecera X-API-Key."""
    if API_SECRET_KEY and x_api_key != API_SECRET_KEY:
        logger.warning("Intento de acceso con API key inválida o ausente.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key inválida o ausente. Incluye la cabecera 'X-API-Key' con el valor correcto.",
        )
    return x_api_key


# ----------------------------------------------------
# 6. INSTANCIACIÓN DE FASTAPI
# ----------------------------------------------------
app = FastAPI(
    title="Servicio de Invitaciones de Calendario APV",
    description="Worker que lee la hoja 'Calendar' y envía invitaciones (.ics) a los APVs por Gmail API.",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {
        "status": "active",
        "service": "APV Calendar Invite Trigger",
        "version": "3.0.0",
        "timezone_configured": DEFAULT_TIMEZONE,
        "auth_enabled": bool(API_SECRET_KEY),
        "gmail_configured": gmail_sender is not None,
        "worker": worker.status,
    }


@app.get("/health")
def health_check():
    """Estado del worker y de la configuración de Gmail."""
    healthy = worker.status["running"] and worker.status["last_error"] is None
    return {
        "status": "healthy" if healthy else "degraded",
        "gmail_configured": gmail_sender is not None,
        "sender": gmail_sender.sender if gmail_sender else None,
        "worker": worker.status,
    }


# ----------------------------------------------------
# 7. ENDPOINT DE RECIBIR WEBHOOK (envío manual/directo)
# ----------------------------------------------------
@app.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_api_key)],
)
def handle_sheets_webhook(payload: WebhookPayload):
    logger.info(
        f"Webhook recibido — APV: {payload.apv}, "
        f"Cliente: {payload.nombre} {payload.apellido}, "
        f"Email: {payload.email_apv}"
    )

    is_mock = gmail_sender is None or os.getenv("TEST_MODE", "").lower() == "true"
    sender_email = gmail_sender.sender if gmail_sender else "mock@example.com"
    tz_str = payload.timezone or DEFAULT_TIMEZONE

    try:
        msg, local_dt, _ = build_invite(payload.model_dump(), sender_email, tz_str)
    except ValueError as e:
        logger.error(f"Error al parsear fecha/hora: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if is_mock:
        logger.info("=== [SIMULACIÓN / MOCK] ENVÍO DE CORREO ===")
        logger.info(f"Para: {payload.email_apv} | Asunto: {msg['Subject']}")
    else:
        try:
            gmail_sender.send(msg)
        except Exception as e:
            logger.error(f"Error enviando por Gmail API: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"No se pudo enviar el correo por Gmail API: {e}",
            )

    logger.info(
        f"Invitación procesada — destino: {payload.email_apv}, "
        f"mock: {is_mock}, fecha: {local_dt.isoformat()}"
    )

    return {
        "success": True,
        "message": (
            f"Invitación enviada con éxito al correo {payload.email_apv}"
            + (" (SIMULACIÓN)" if is_mock else "")
        ),
        "mock_mode": is_mock,
        "appointment": {
            "apv": payload.apv,
            "cliente": f"{payload.nombre} {payload.apellido}",
            "fecha_hora_local": local_dt.isoformat(),
            "unidad": payload.unidad_de_interes,
        },
    }


# Arrancar localmente usando uvicorn si se ejecuta este archivo
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
