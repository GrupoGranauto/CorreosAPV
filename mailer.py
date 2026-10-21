"""
Construcción y envío de invitaciones de calendario para los APV.

- build_invite(): arma el correo MIME (texto + HTML con logo + text/calendar).
- GmailSender: envía el MIME por la Gmail API (HTTPS). Railway bloquea los
  puertos SMTP en planes sin costo, por eso no se usa smtplib.
"""
import base64
import datetime as dt
import html
import logging
import os
import time
import uuid
import zoneinfo
from datetime import datetime, timedelta
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from icalendar import Alarm, Calendar, Event, vCalAddress, vText

logger = logging.getLogger("apv_calendars.mailer")

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
LOGO_PATH = Path(__file__).parent / "AUTOINSIGHTS-LOGO-03.png"


# ----------------------------------------------------
# FECHAS
# ----------------------------------------------------
def get_timezone(tz_str: str) -> zoneinfo.ZoneInfo:
    try:
        return zoneinfo.ZoneInfo(tz_str)
    except Exception:
        return zoneinfo.ZoneInfo("America/Mexico_City")


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
                time_obj = dt.time(hour, minute, second)
        except Exception:
            pass

    if not time_obj:
        raise ValueError(
            f"Formato de hora no reconocido: '{time_str}'. "
            "Formatos soportados: HH:MM, HH:MM:SS, hh:mm AM/PM"
        )

    return datetime.combine(date_obj, time_obj).replace(tzinfo=get_timezone(tz_str))


# ----------------------------------------------------
# CONSTRUCCIÓN DEL CORREO
# ----------------------------------------------------
def build_invite(cita: dict, sender: str, tz_str: str):
    """
    Arma el correo de invitación.

    `cita` requiere: apv, nombre, apellido, contacto_cliente, unidad_de_interes,
    agencia_de_cita, bdc_responsable, dia_de_cita, hora_de_cita, email_apv.

    Regresa (msg, local_dt, description).
    """
    local_dt = parse_appointment_datetime(cita["dia_de_cita"], cita["hora_de_cita"], tz_str)
    end_dt = local_dt + timedelta(minutes=60)
    utc = zoneinfo.ZoneInfo("UTC")

    apv = cita["apv"]
    cliente = f"{cita['nombre']} {cita['apellido']}".strip()

    # ── Descripción (texto plano y cuerpo del evento) ──
    desc_lines = [
        "Detalles de la Cita de Cliente:",
        "----------------------------------------",
        f"Asesor APV: {apv}",
        f"Cliente: {cliente}",
        f"Contacto del Cliente: {cita['contacto_cliente']}",
        f"Vehículo / Unidad de Interés: {cita['unidad_de_interes']}",
        f"Agencia / Sucursal de Cita: {cita['agencia_de_cita']}",
        f"BDC Responsable: {cita['bdc_responsable']}",
        f"Fecha y Hora: {local_dt.strftime('%d/%m/%Y %H:%M')} ({tz_str})",
        "----------------------------------------",
        "Por favor, confirma tu asistencia respondiendo a este evento.",
    ]
    description = "\n".join(desc_lines)

    # ── iCalendar (.ics) ──
    cal = Calendar()
    cal.add("prodid", "-//AutoInsights//APV Calendars//ES")
    cal.add("version", "2.0")
    cal.add("method", "REQUEST")

    event = Event()
    event.add("uid", f"apv-cita-{uuid.uuid4()}")
    event.add("dtstamp", datetime.now(utc))
    event.add("dtstart", local_dt.astimezone(utc))
    event.add("dtend", end_dt.astimezone(utc))
    event.add("summary", f"Cita Cliente: {cliente} - APV {apv}")
    event.add("description", description)
    event.add("location", cita["agencia_de_cita"])
    event.add("status", "CONFIRMED")
    event.add("sequence", 0)

    organizer = vCalAddress(f"MAILTO:{sender}")
    organizer.params["CN"] = vText("Sistema de Citas BDC")
    event["organizer"] = organizer

    attendee = vCalAddress(f"MAILTO:{cita['email_apv']}")
    attendee.params["ROLE"] = vText("REQ-PARTICIPANT")
    attendee.params["PARTSTAT"] = vText("NEEDS-ACTION")
    attendee.params["RSVP"] = vText("TRUE")
    attendee.params["CN"] = vText(apv)
    event.add("attendee", attendee)

    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", "Recordatorio de Cita con Cliente")
    alarm.add("trigger", timedelta(minutes=-15))
    event.add_component(alarm)

    cal.add_component(event)
    ics_data = cal.to_ical()

    # ── HTML (valores escapados: vienen de Kommo / Sheets) ──
    e = {k: html.escape(str(v)) for k, v in cita.items()}
    logo_data = LOGO_PATH.read_bytes() if LOGO_PATH.exists() else None
    logo_img_tag = (
        '<img src="cid:logo@apv" alt="AutoInsights" style="max-width: 320px; height: auto; margin-bottom: 10px;">'
        if logo_data else ""
    )
    fecha_html = local_dt.strftime("%d/%m/%Y %I:%M %p")

    html_body = f"""
        <html>
          <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #1e293b; max-width: 600px; margin: 0 auto; padding: 0; background-color: #f8fafc;">
            <div style="background: linear-gradient(135deg, #493F91 0%, #6B5FC7 100%); color: #ffffff; padding: 30px; text-align: center; border-radius: 12px 12px 0 0; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
              {logo_img_tag}
              <h2 style="margin: 10px 0 0 0; font-size: 24px; font-weight: 600; letter-spacing: -0.5px;">Nueva Cita de Cliente</h2>
              <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 15px;">Se ha agendado un evento en tu calendario</p>
            </div>

            <div style="background-color: #ffffff; padding: 30px; border-left: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
              <p style="margin-top: 0; font-size: 16px;">Hola <strong>{e['apv']}</strong>,</p>
              <p style="font-size: 15px;">El equipo de BDC ha asignado una cita de cliente a tu correo. A continuación puedes revisar la ficha de información:</p>

              <div style="background-color: #f1f5f9; border-radius: 8px; padding: 20px; margin: 20px 0;">
                <table style="width: 100%; border-collapse: collapse;">
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; width: 40%; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Cliente</td>
                    <td style="padding: 8px 0; font-weight: 600; color: #0f172a; font-size: 15px;">{e['nombre']} {e['apellido']}</td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Contacto</td>
                    <td style="padding: 8px 0; color: #0f172a; font-size: 15px;"><a href="tel:{e['contacto_cliente']}" style="color: #493F91; text-decoration: none; font-weight: 500;">{e['contacto_cliente']}</a></td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Unidad de Interés</td>
                    <td style="padding: 8px 0; color: #0f172a; font-size: 15px; font-weight: 500;">{e['unidad_de_interes']}</td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Agencia de Cita</td>
                    <td style="padding: 8px 0; color: #0f172a; font-size: 15px;">{e['agencia_de_cita']}</td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">BDC Responsable</td>
                    <td style="padding: 8px 0; color: #0f172a; font-size: 15px;">{e['bdc_responsable']}</td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Fecha y Hora</td>
                    <td style="padding: 8px 0; color: #493F91; font-size: 15px; font-weight: 600;">{fecha_html}</td>
                  </tr>
                </table>
              </div>

              <div style="background-color: #EEEDFA; border-left: 4px solid #493F91; color: #342E6B; padding: 15px; border-radius: 4px; font-size: 14px; margin: 25px 0 10px 0;">
                &#128161; <strong>¿Cómo agendarla?</strong> Gmail ha reconocido este correo como una invitación de calendario. Selecciona <strong>"Sí"</strong> (o "Aceptar") en la cabecera de este correo para que se añada de forma automática a tu agenda en tu dispositivo móvil y computadora.
              </div>
            </div>

            <div style="background-color: #f1f5f9; padding: 20px; text-align: center; border-radius: 0 0 12px 12px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #64748b;">
              <p style="margin: 0;">Este es un correo automatizado enviado por el Sistema de Citas BDC.</p>
            </div>
          </body>
        </html>
        """

    # ── Estructura MIME ──
    # multipart/alternative (raíz)
    #   ├── text/plain
    #   ├── multipart/related  (HTML + logo embebido)
    #   └── text/calendar      (ÚLTIMA alternativa → Gmail muestra Sí/No/Quizás)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Invitación: Cita con {cliente} - {local_dt.strftime('%d/%m/%Y %H:%M')}"
    msg["From"] = sender
    msg["To"] = cita["email_apv"]

    msg.attach(MIMEText(description, "plain", "utf-8"))

    related_part = MIMEMultipart("related")
    related_part.attach(MIMEText(html_body, "html", "utf-8"))
    if logo_data:
        logo_img = MIMEImage(logo_data, _subtype="png")
        logo_img.add_header("Content-ID", "<logo@apv>")
        logo_img.add_header("Content-Disposition", "inline", filename="logo.png")
        related_part.attach(logo_img)
    msg.attach(related_part)

    cal_part = MIMEText(ics_data.decode("utf-8"), "calendar", "utf-8")
    cal_part.set_param("method", "REQUEST")
    msg.attach(cal_part)

    return msg, local_dt, description


# ----------------------------------------------------
# ENVÍO POR GMAIL API
# ----------------------------------------------------
class GmailSender:
    """Envía mensajes MIME con la Gmail API usando un refresh token OAuth."""

    def __init__(self, sender: str, client_id: str, client_secret: str, refresh_token: str):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.credentials import Credentials

        self.sender = sender
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=[GMAIL_SEND_SCOPE],
        )
        self._session = AuthorizedSession(creds)

    @classmethod
    def from_env(cls):
        """Crea el sender con variables de entorno. Regresa None si faltan."""
        sender = os.getenv("GMAIL_SENDER", "").strip()
        client_id = os.getenv("GMAIL_CLIENT_ID", "").strip()
        client_secret = os.getenv("GMAIL_CLIENT_SECRET", "").strip()
        refresh_token = os.getenv("GMAIL_REFRESH_TOKEN", "").strip()
        if not all([sender, client_id, client_secret, refresh_token]):
            return None
        return cls(sender, client_id, client_secret, refresh_token)

    def send(self, msg, max_retries: int = 3) -> str:
        """Envía el mensaje. Regresa el id del mensaje de Gmail."""
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = self._session.post(GMAIL_SEND_URL, json={"raw": raw}, timeout=30)
                if resp.status_code == 200:
                    msg_id = resp.json().get("id", "")
                    logger.info(f"Correo enviado a {msg['To']} (gmail id {msg_id})")
                    return msg_id
                last_error = RuntimeError(f"Gmail API {resp.status_code}: {resp.text[:300]}")
                # 4xx (salvo 429) no se arregla reintentando
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    break
            except Exception as err:
                last_error = err
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(f"Envío Gmail {attempt}/{max_retries} falló: {last_error}. Reintento en {wait}s")
                time.sleep(wait)
        raise last_error
