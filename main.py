import os
import re
import uuid
import time
import logging
import zoneinfo
import smtplib
import socket
from datetime import datetime, timedelta
import base64
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
from typing import Optional

# --- PARCHE PARA RAILWAY (Forzar IPv4) ---
# Railway a veces intenta usar IPv6 para smtp.gmail.com y falla con "Network is unreachable".
# Esto obliga a Python a usar solo IPv4.
old_getaddrinfo = socket.getaddrinfo
def new_getaddrinfo(*args, **kwargs):
    responses = old_getaddrinfo(*args, **kwargs)
    return [res for res in responses if res[0] == socket.AF_INET]
socket.getaddrinfo = new_getaddrinfo
# -----------------------------------------

from fastapi import FastAPI, HTTPException, status, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings
from icalendar import Calendar, Event, vCalAddress, vText, Alarm

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
# 2. CONFIGURACIÓN Y VARIABLES DE ENTORNO
# ----------------------------------------------------
class Settings(BaseSettings):
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_user: str
    smtp_pass: str
    default_timezone: str = "America/Mexico_City"
    api_secret_key: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

# Inicializar configuración
try:
    settings = Settings()
    logger.info("Configuración cargada correctamente.")
    if settings.api_secret_key:
        logger.info("Autenticación por API key ACTIVADA.")
    else:
        logger.warning(
            "API_SECRET_KEY no configurada — el endpoint /webhook está "
            "ABIERTO sin autenticación."
        )
except Exception as e:
    logger.error(f"No se pudieron cargar las variables de entorno: {e}")
    logger.error("Asegúrate de configurar las variables en .env o en Railway.")
    settings = None

# ----------------------------------------------------
# 3. DEFINICIÓN DEL PAYLOAD DE ENTRADA (WEBHOOK)
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
# 4. FUNCIONES AUXILIARES
# ----------------------------------------------------
def parse_appointment_datetime(date_str: str, time_str: str, tz_str: str) -> datetime:
    """Parsea la fecha y hora recibidas en múltiples formatos posibles."""
    date_str = date_str.strip()
    time_str = time_str.strip()

    # Intentar parsear fecha
    date_obj = None
    date_formats = ["%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"]
    for fmt in date_formats:
        try:
            date_obj = datetime.strptime(date_str, fmt).date()
            break
        except ValueError:
            continue

    if not date_obj:
        raise ValueError(
            f"Formato de fecha no reconocido: '{date_str}'. "
            "Formatos soportados: YYYY-MM-DD, DD/MM/YYYY"
        )

    # Intentar parsear hora
    time_obj = None
    time_formats = [
        "%H:%M:%S", "%H:%M",
        "%I:%M:%S %p", "%I:%M %p",
        "%I:%M:%S%p", "%I:%M%p",
    ]
    for fmt in time_formats:
        try:
            time_obj = datetime.strptime(time_str, fmt).time()
            break
        except ValueError:
            continue

    # Fallback por si la hora tiene milisegundos o formato no estándar
    if not time_obj:
        try:
            parts = time_str.split(":")
            if len(parts) >= 2:
                hour = int(parts[0])
                minute = int(parts[1])
                second = 0
                if len(parts) >= 3:
                    sec_clean = "".join(c for c in parts[2] if c.isdigit())
                    if sec_clean:
                        second = int(sec_clean[:2])
                import datetime as dt
                time_obj = dt.time(hour, minute, second)
        except Exception:
            pass

    if not time_obj:
        raise ValueError(
            f"Formato de hora no reconocido: '{time_str}'. "
            "Formatos soportados: HH:MM, HH:MM:SS, hh:mm AM/PM"
        )

    # Combinar fecha y hora
    local_dt = datetime.combine(date_obj, time_obj)

    # Asignar zona horaria
    try:
        tz = zoneinfo.ZoneInfo(tz_str)
    except Exception:
        tz = zoneinfo.ZoneInfo("America/Mexico_City")

    return local_dt.replace(tzinfo=tz)


def send_email_with_retry(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    recipient: str,
    msg,
    max_retries: int = 3,
):
    """Envía un correo electrónico con reintentos y backoff exponencial."""
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            if smtp_port == 465:
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_user, [recipient], msg.as_string())
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_user, [recipient], msg.as_string())
            logger.info(
                f"Correo enviado exitosamente a {recipient} "
                f"(intento {attempt}/{max_retries})"
            )
            return
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    f"Intento SMTP {attempt}/{max_retries} falló con {type(e).__name__}: {e}. "
                    f"Reintentando en {wait}s..."
                )
                logger.error("--- DETALLE DEL ERROR SMTP ---", exc_info=True)
                time.sleep(wait)
            else:
                logger.error(
                    f"Todos los intentos de envío SMTP fallaron "
                    f"({max_retries}/{max_retries}): {e}",
                    exc_info=True
                )
    raise last_exception


# ----------------------------------------------------
# 5. DEPENDENCIA DE AUTENTICACIÓN POR API KEY
# ----------------------------------------------------
async def verify_api_key(x_api_key: Optional[str] = Header(default=None)):
    """Valida la API key enviada en la cabecera X-API-Key."""
    if settings and settings.api_secret_key:
        if not x_api_key or x_api_key != settings.api_secret_key:
            logger.warning("Intento de acceso con API key inválida o ausente.")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "API key inválida o ausente. "
                    "Incluye la cabecera 'X-API-Key' con el valor correcto."
                ),
            )
    return x_api_key


# ----------------------------------------------------
# 6. INSTANCIACIÓN DE FASTAPI
# ----------------------------------------------------
app = FastAPI(
    title="Servicio de Invitaciones de Calendario APV",
    description="API que recibe webhooks de Google Sheets y envía invitaciones SMTP (.ics) a los APVs.",
    version="2.0.0",
)

# Habilitar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {
        "status": "active",
        "service": "APV Calendar Invite Trigger",
        "version": "2.0.0",
        "timezone_configured": settings.default_timezone if settings else "N/A",
        "auth_enabled": bool(settings and settings.api_secret_key),
    }


@app.get("/health")
def health_check():
    """Endpoint de health check que verifica la conectividad SMTP."""
    smtp_reachable = False
    smtp_error = None
    if settings:
        try:
            if settings.smtp_port == 465:
                with smtplib.SMTP_SSL(
                    settings.smtp_host, settings.smtp_port, timeout=5
                ) as server:
                    smtp_reachable = True
            else:
                with smtplib.SMTP(
                    settings.smtp_host, settings.smtp_port, timeout=5
                ) as server:
                    server.starttls()
                    smtp_reachable = True
        except Exception as e:
            smtp_error = str(e)

    return {
        "status": "healthy" if smtp_reachable else "degraded",
        "smtp_reachable": smtp_reachable,
        "smtp_error": smtp_error,
        "settings_loaded": settings is not None,
    }


# ----------------------------------------------------
# 7. ENDPOINT DE RECIBIR WEBHOOK
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

    # Verificar si estamos en modo simulación (mock/test)
    is_mock = (
        not settings
        or not settings.smtp_user
        or not settings.smtp_pass
        or settings.smtp_user == "tu_correo@gmail.com"
        or settings.smtp_pass == "tu_contrasena_de_aplicacion_aqui"
        or os.getenv("TEST_MODE", "").lower() == "true"
    )

    # 1. Parsear la fecha y hora de la cita
    tz_str = payload.timezone or settings.default_timezone
    try:
        local_dt = parse_appointment_datetime(
            payload.dia_de_cita, payload.hora_de_cita, tz_str
        )
    except ValueError as e:
        logger.error(f"Error al parsear fecha/hora: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )

    # 2. Generar el archivo iCalendar (.ics)
    try:
        # Calcular fin del evento (duración de 1 hora por defecto)
        end_dt = local_dt + timedelta(minutes=60)

        # Convertir a UTC para cumplir con la especificación de invitación global
        start_utc = local_dt.astimezone(zoneinfo.ZoneInfo("UTC"))
        end_utc = end_dt.astimezone(zoneinfo.ZoneInfo("UTC"))

        # Crear Calendario
        cal = Calendar()
        cal.add("prodid", "-//Antigravity AI//APV Calendars//ES")
        cal.add("version", "2.0")
        cal.add("method", "REQUEST")

        # Crear Evento
        event = Event()
        event_uid = f"apv-cita-{uuid.uuid4()}"
        event.add("uid", event_uid)
        event.add("dtstamp", datetime.now(zoneinfo.ZoneInfo("UTC")))
        event.add("dtstart", start_utc)
        event.add("dtend", end_utc)

        summary = (
            f"Cita Cliente: {payload.nombre} {payload.apellido} "
            f"- APV {payload.apv}"
        )
        event.add("summary", summary)

        # Crear descripción detallada para el cuerpo del evento
        desc_lines = [
            "Detalles de la Cita de Cliente:",
            "----------------------------------------",
            f"Asesor APV: {payload.apv}",
            f"Cliente: {payload.nombre} {payload.apellido}",
            f"Contacto del Cliente: {payload.contacto_cliente}",
            f"Vehículo / Unidad de Interés: {payload.unidad_de_interes}",
            f"Agencia / Sucursal de Cita: {payload.agencia_de_cita}",
            f"BDC Responsable: {payload.bdc_responsable}",
            f"Fecha y Hora: {local_dt.strftime('%d/%m/%Y %H:%M')} ({tz_str})",
            "----------------------------------------",
            "Por favor, confirma tu asistencia respondiendo a este evento.",
        ]
        description = "\n".join(desc_lines)
        event.add("description", description)
        event.add("location", payload.agencia_de_cita)
        event.add("status", "CONFIRMED")
        event.add("sequence", 0)

        # Configurar Organizador (La cuenta de correo desde la que se manda el SMTP)
        organizer = vCalAddress(f"MAILTO:{settings.smtp_user}")
        organizer.params["CN"] = vText("Sistema de Citas BDC")
        event["organizer"] = organizer

        # Configurar Asistente (El APV)
        attendee = vCalAddress(f"MAILTO:{payload.email_apv}")
        attendee.params["ROLE"] = vText("REQ-PARTICIPANT")
        attendee.params["PARTSTAT"] = vText("NEEDS-ACTION")
        attendee.params["RSVP"] = vText("TRUE")
        attendee.params["CN"] = vText(payload.apv)
        event.add("attendee", attendee)

        # Añadir alarma/recordatorio (15 minutos antes)
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", "Recordatorio de Cita con Cliente")
        alarm.add("trigger", timedelta(minutes=-15))
        event.add_component(alarm)

        # Agregar evento al calendario
        cal.add_component(event)
        ics_data = cal.to_ical()

    except Exception as e:
        logger.error(f"Error al generar iCal: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al generar la invitación de calendario (.ics): {str(e)}",
        )

    # 3. Construir y enviar el correo electrónico por SMTP
    try:
        # ── Cargar logo como imagen embebida (CID) ──
        logo_path = Path(__file__).parent / "AUTOINSIGHTS-LOGO-03.png"
        logo_data = logo_path.read_bytes() if logo_path.exists() else None

        # ── Estructura MIME ──
        # multipart/alternative (raíz)
        #   ├── text/plain
        #   ├── multipart/related  (HTML + imágenes embebidas)
        #   │     ├── text/html    (referencia cid:logo)
        #   │     └── image/png    (Content-ID: <logo@apv>)
        #   └── text/calendar      (ÚLTIMA alternativa → Gmail RSVP)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = (
            f"Invitación: Cita con {payload.nombre} {payload.apellido}"
            f" - {local_dt.strftime('%d/%m/%Y %H:%M')}"
        )
        msg["From"] = settings.smtp_user
        msg["To"] = payload.email_apv

        # 1) Texto plano (fallback básico)
        txt_part = MIMEText(description, "plain", "utf-8")
        msg.attach(txt_part)

        # 2) HTML con logo embebido dentro de multipart/related
        logo_img_tag = '<img src="cid:logo@apv" alt="AutoInsights" style="max-width: 320px; height: auto; margin-bottom: 10px;">' if logo_data else ''

        html_body = f"""
        <html>
          <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #1e293b; max-width: 600px; margin: 0 auto; padding: 0; background-color: #f8fafc;">
            <div style="background: linear-gradient(135deg, #493F91 0%, #6B5FC7 100%); color: #ffffff; padding: 30px; text-align: center; border-radius: 12px 12px 0 0; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
              {logo_img_tag}
              <h2 style="margin: 10px 0 0 0; font-size: 24px; font-weight: 600; letter-spacing: -0.5px;">Nueva Cita de Cliente</h2>
              <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 15px;">Se ha agendado un evento en tu calendario</p>
            </div>

            <div style="background-color: #ffffff; padding: 30px; border-left: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
              <p style="margin-top: 0; font-size: 16px;">Hola <strong>{payload.apv}</strong>,</p>
              <p style="font-size: 15px;">El equipo de BDC ha asignado una cita de cliente a tu correo. A continuación puedes revisar la ficha de información:</p>

              <div style="background-color: #f1f5f9; border-radius: 8px; padding: 20px; margin: 20px 0;">
                <table style="width: 100%; border-collapse: collapse;">
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; width: 40%; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Cliente</td>
                    <td style="padding: 8px 0; font-weight: 600; color: #0f172a; font-size: 15px;">{payload.nombre} {payload.apellido}</td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Contacto</td>
                    <td style="padding: 8px 0; color: #0f172a; font-size: 15px;"><a href="tel:{payload.contacto_cliente}" style="color: #493F91; text-decoration: none; font-weight: 500;">{payload.contacto_cliente}</a></td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Unidad de Interés</td>
                    <td style="padding: 8px 0; color: #0f172a; font-size: 15px; font-weight: 500;">{payload.unidad_de_interes}</td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Agencia de Cita</td>
                    <td style="padding: 8px 0; color: #0f172a; font-size: 15px;">{payload.agencia_de_cita}</td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">BDC Responsable</td>
                    <td style="padding: 8px 0; color: #0f172a; font-size: 15px;">{payload.bdc_responsable}</td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Fecha y Hora</td>
                    <td style="padding: 8px 0; color: #493F91; font-size: 15px; font-weight: 600;">{local_dt.strftime('%d/%m/%Y %I:%M %p')}</td>
                  </tr>
                </table>
              </div>

              <div style="background-color: #EEEDFA; border-left: 4px solid #493F91; color: #342E6B; padding: 15px; border-radius: 4px; font-size: 14px; margin: 25px 0 10px 0;">
                \U0001f4a1 <strong>¿Cómo agendarla?</strong> Gmail ha reconocido este correo como una invitación de calendario. Selecciona <strong>"Sí"</strong> (o "Aceptar") en la cabecera de este correo para que se añada de forma automática a tu agenda en tu dispositivo móvil y computadora.
              </div>
            </div>

            <div style="background-color: #f1f5f9; padding: 20px; text-align: center; border-radius: 0 0 12px 12px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #64748b;">
              <p style="margin: 0;">Este es un correo automatizado enviado por el Sistema de Citas BDC.</p>
            </div>
          </body>
        </html>
        """

        # Envolver HTML + logo en multipart/related
        related_part = MIMEMultipart("related")
        html_part = MIMEText(html_body, "html", "utf-8")
        related_part.attach(html_part)

        if logo_data:
            logo_img = MIMEImage(logo_data, _subtype="png")
            logo_img.add_header("Content-ID", "<logo@apv>")
            logo_img.add_header("Content-Disposition", "inline", filename="logo.png")
            related_part.attach(logo_img)

        msg.attach(related_part)

        # 3) Invitación de calendario INLINE (DEBE ser la última alternativa)
        cal_part = MIMEText(ics_data.decode("utf-8"), "calendar", "utf-8")
        cal_part.set_param("method", "REQUEST")
        msg.attach(cal_part)

        # Enviar el correo o simular envío
        if is_mock:
            logger.info("=== [SIMULACIÓN / MOCK] ENVÍO DE CORREO ===")
            logger.info(
                f"De: {settings.smtp_user if settings else 'mock@example.com'}"
            )
            logger.info(f"Para: {payload.email_apv}")
            logger.info(f"Asunto: {msg['Subject']}")
            logger.debug("--- CONTENIDO ICS GENERADO ---")
            logger.debug(ics_data.decode("utf-8", errors="replace"))
        else:
            send_email_with_retry(
                smtp_host=settings.smtp_host,
                smtp_port=settings.smtp_port,
                smtp_user=settings.smtp_user,
                smtp_pass=settings.smtp_pass,
                recipient=payload.email_apv,
                msg=msg,
            )

    except HTTPException:
        raise
    except Exception as e:
        if not is_mock:
            logger.error(f"Error SMTP: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Error de red SMTP al enviar el correo al APV: {str(e)}",
            )
        else:
            logger.warning(f"Error SMTP simulado (ignorado en modo test): {e}")

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
