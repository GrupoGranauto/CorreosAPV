import os
import re
import sys
import time
import logging
import base64
import smtplib
import socket
import zoneinfo
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Optional

# Cargar variables de entorno
from dotenv import load_dotenv
load_dotenv()

# Instalar gspread y google-auth si no están
try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    print("Error: Se requieren las librerías 'gspread' y 'google-auth'.")
    print("Ejecuta: pip install gspread google-auth")
    sys.exit(1)

# --- Configuración de Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sheet_worker")

# --- Rutas y Configuración de Google Sheets ---
CREDS_PATH = r"G:\Unidades compartidas\BDC Grupo Granauto\BDC Business Intelligence\credenciales\credencialesAI.json"
SPREADSHEET_ID = "1o_gr48pIWG5hrVf51Q5R8RPTa4eJL6f6a0Whk-2LGN0"
SHEET_NAME = "Calendar"

# --- Configuración SMTP ---
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "notificaciones@autoinsights.mx")
SMTP_PASS = os.getenv("SMTP_PASS", "lioh ayni qxzm buoh")
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/Mexico_City")

# --- Validaciones y Regex ---
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

def get_sheet_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if os.path.exists(CREDS_PATH):
        creds = Credentials.from_service_account_file(CREDS_PATH, scopes=scopes)
    else:
        raise FileNotFoundError(f"No se encontró el archivo de credenciales en {CREDS_PATH}")
    
    client = gspread.authorize(creds)
    return client

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
        raise ValueError(f"Formato de fecha no reconocido: '{date_str}'")

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

    if not time_obj:
        # Fallback simple
        try:
            parts = time_str.split(":")
            if len(parts) >= 2:
                hour = int(parts[0])
                minute = int(parts[1])
                time_obj = datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M").time()
        except Exception:
            pass

    if not time_obj:
        raise ValueError(f"Formato de hora no reconocido: '{time_str}'")

    # Combinar fecha y hora
    local_dt = datetime.combine(date_obj, time_obj)
    try:
        tz = zoneinfo.ZoneInfo(tz_str)
    except Exception:
        tz = zoneinfo.ZoneInfo("America/Mexico_City")

    return local_dt.replace(tzinfo=tz)

def send_email_with_retry(smtp_host: str, smtp_port: int, smtp_user: str, smtp_pass: str, recipient: str, msg, max_retries: int = 3):
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Iniciando conexión SMTP hacia {smtp_host}:{smtp_port}... (Intento {attempt}/{max_retries})")
            if smtp_port == 465:
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_user, [recipient], msg.as_string())
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_user, [recipient], msg.as_string())
            logger.info(f"Correo enviado exitosamente a {recipient}")
            return True
        except Exception as e:
            last_exception = e
            logger.warning(f"Intento SMTP {attempt} falló con: {e}")
            if attempt < max_retries:
                time.sleep(2)
    raise last_exception

def process_row(ws, row_idx, row_data):
    """Procesa una fila de la hoja de cálculo, genera el ICS y envía el correo."""
    try:
        apv = row_data[0].strip()
        nombre = row_data[1].strip()
        apellido = row_data[2].strip()
        contacto_cliente = row_data[3].strip()
        unidad_de_interes = row_data[4].strip()
        agencia_de_cita = row_data[5].strip()
        bdc_responsable = row_data[7].strip()
        dia_de_cita = row_data[8].strip()
        hora_de_cita = row_data[9].strip()
        email_apv = row_data[10].strip()

        logger.info(f"Procesando Fila {row_idx} — APV: {apv}, Cliente: {nombre} {apellido}")

        if not email_apv or not dia_de_cita or not hora_de_cita:
            raise ValueError("Faltan datos obligatorios (Email APV, Día o Hora de cita)")
        
        if not EMAIL_REGEX.match(email_apv):
            raise ValueError(f"El email del APV '{email_apv}' no tiene un formato válido.")

        # 1. Parsear Fecha y Hora
        local_dt = parse_appointment_datetime(dia_de_cita, hora_de_cita, DEFAULT_TIMEZONE)
        end_dt = local_dt + timedelta(minutes=60)
        utc_start = local_dt.astimezone(zoneinfo.ZoneInfo("UTC"))
        utc_end = end_dt.astimezone(zoneinfo.ZoneInfo("UTC"))

        # 2. Generar invitación iCalendar (.ics) de forma nativa sencilla
        # Para evitar dependencias complejas, armamos la estructura iCal en texto plano
        uid = f"{datetime.now().strftime('%Y%m%dT%H%M%S')}--{row_idx}@autoinsights.mx"
        dtstamp = datetime.now(zoneinfo.ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
        dtstart = utc_start.strftime("%Y%m%dT%H%M%SZ")
        dtend = utc_end.strftime("%Y%m%dT%H%M%SZ")

        # Texto de descripción del evento
        desc_lines = [
            f"Ficha de Cita de Cliente asignada a ti:",
            "----------------------------------------",
            f"APV Asignado: {apv}",
            f"Cliente: {nombre} {apellido}",
            f"Contacto del Cliente: {contacto_cliente}",
            f"Vehículo / Unidad de Interés: {unidad_de_interes}",
            f"Agencia / Sucursal de Cita: {agencia_de_cita}",
            f"BDC Responsable: {bdc_responsable}",
            f"Fecha y Hora: {local_dt.strftime('%d/%m/%Y %H:%M')} ({DEFAULT_TIMEZONE})",
            "----------------------------------------",
            "Por favor, confirma tu asistencia respondiendo a este evento.",
        ]
        description = "\\n".join(desc_lines)

        ics_lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//AutoInsights//BDC System//ES",
            "METHOD:REQUEST",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
            f"SUMMARY:Invitación: Cita con {nombre} {apellido}",
            f"DESCRIPTION:{description}",
            f"LOCATION:{agencia_de_cita}",
            "STATUS:CONFIRMED",
            "SEQUENCE:0",
            f"ORGANIZER;CN=Sistema de Citas BDC:MAILTO:{SMTP_USER}",
            f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN={apv}:MAILTO:{email_apv}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "DESCRIPTION:Recordatorio de Cita",
            "TRIGGER:-PT15M",
            "END:VALARM",
            "END:VEVENT",
            "END:VCALENDAR"
        ]
        ics_content = "\r\n".join(ics_lines).encode("utf-8")

        # 3. Construir Correo MIME
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Invitación: Cita con {nombre} {apellido} - {local_dt.strftime('%d/%m/%Y %H:%M')}"
        msg["From"] = SMTP_USER
        msg["To"] = email_apv

        # Parte texto plano
        txt_part = MIMEText("\n".join(desc_lines), "plain", "utf-8")
        msg.attach(txt_part)

        # Parte HTML
        logo_path = Path(__file__).parent / "AUTOINSIGHTS-LOGO-03.png"
        logo_data = logo_path.read_bytes() if logo_path.exists() else None
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
              <p style="margin-top: 0; font-size: 16px;">Hola <strong>{apv}</strong>,</p>
              <p style="font-size: 15px;">El equipo de BDC ha asignado una cita de cliente a tu correo. A continuación puedes revisar la ficha de información:</p>

              <div style="background-color: #f1f5f9; border-radius: 8px; padding: 20px; margin: 20px 0;">
                <table style="width: 100%; border-collapse: collapse;">
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; width: 40%; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Cliente</td>
                    <td style="padding: 8px 0; font-weight: 600; color: #0f172a; font-size: 15px;">{nombre} {apellido}</td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Contacto</td>
                    <td style="padding: 8px 0; color: #0f172a; font-size: 15px;"><a href="tel:{contacto_cliente}" style="color: #493F91; text-decoration: none; font-weight: 500;">{contacto_cliente}</a></td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Unidad de Interés</td>
                    <td style="padding: 8px 0; color: #0f172a; font-size: 15px; font-weight: 500;">{unidad_de_interes}</td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Agencia de Cita</td>
                    <td style="padding: 8px 0; color: #0f172a; font-size: 15px;">{agencia_de_cita}</td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">BDC Responsable</td>
                    <td style="padding: 8px 0; color: #0f172a; font-size: 15px;">{bdc_responsable}</td>
                  </tr>
                  <tr>
                    <td style="padding: 8px 0; font-weight: 600; color: #475569; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Fecha y Hora</td>
                    <td style="padding: 8px 0; color: #493F91; font-size: 15px; font-weight: 600;">{local_dt.strftime('%d/%m/%Y %I:%M %p')}</td>
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

        related_part = MIMEMultipart("related")
        html_part = MIMEText(html_body, "html", "utf-8")
        related_part.attach(html_part)

        if logo_data:
            logo_img = MIMEImage(logo_data, _subtype="png")
            logo_img.add_header("Content-ID", "<logo@apv>")
            logo_img.add_header("Content-Disposition", "inline", filename="logo.png")
            related_part.attach(logo_img)

        msg.attach(related_part)

        # Invitación de calendario INLINE
        cal_part = MIMEText(ics_content.decode("utf-8"), "calendar", "utf-8")
        cal_part.set_param("method", "REQUEST")
        msg.attach(cal_part)

        # 4. Enviar Correo
        send_email_with_retry(
            smtp_host=SMTP_HOST,
            smtp_port=SMTP_PORT,
            smtp_user=SMTP_USER,
            smtp_pass=SMTP_PASS,
            recipient=email_apv,
            msg=msg
        )

        # 5. Marcar como Enviado en la hoja
        ahora_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        ws.update_cell(row_idx, 13, f"Enviado ✓ — {ahora_str}")
        logger.info(f"Fila {row_idx} marcada como ENVIADO en Sheets.")
        return True

    except Exception as err:
        logger.error(f"Error procesando Fila {row_idx}: {err}", exc_info=True)
        # Escribir error en la celda de estado
        try:
            ws.update_cell(row_idx, 13, f"ERROR: {str(err)[:100]}")
            # Desmarcar casilla para no reintentar infinitamente
            ws.update_cell(row_idx, 12, False)
        except Exception as sheet_err:
            logger.error(f"No se pudo actualizar error en Sheets para Fila {row_idx}: {sheet_err}")
        return False

def main_loop():
    logger.info("Iniciando Worker de Citas BDC (Lectura directa de Sheets)...")
    logger.info(f"Spreadsheet ID : {SPREADSHEET_ID}")
    logger.info(f"Hoja           : {SHEET_NAME}")
    logger.info(f"Credenciales   : {CREDS_PATH}")

    # Verificar credenciales y cargar cliente
    try:
        client = get_sheet_client()
    except Exception as e:
        logger.critical(f"No se pudo inicializar cliente de Sheets: {e}", exc_info=True)
        sys.exit(1)

    while True:
        try:
            # 1. Obtener la hoja
            sh = client.open_by_key(SPREADSHEET_ID)
            ws = sh.worksheet(SHEET_NAME)

            # 2. Leer todos los valores
            raw_values = ws.get_all_values()
            if len(raw_values) <= 1:
                time.sleep(10)
                continue

            # 3. Escanear filas con Enviar = TRUE y Estado != Enviado
            for idx, row in enumerate(raw_values[1:], start=2):
                if len(row) < 12:
                    continue

                enviar_val = row[11].strip().upper()
                estado_val = row[12].strip() if len(row) > 12 else ""

                # Procesar si Enviar es verdadero y no se ha enviado exitosamente
                if enviar_val in ("TRUE", "VERDADERO", "1") and not estado_val.startswith("Enviado"):
                    # Marcar como Procesando inmediatamente para evitar colisiones
                    ws.update_cell(idx, 13, "Procesando...")
                    
                    # Ejecutar envío
                    process_row(ws, idx, row)
                    
                    # Pausa breve entre envíos para no saturar SMTP
                    time.sleep(2)

        except Exception as e:
            logger.error(f"Error en el ciclo principal: {e}")
            # Si el token expira o hay pérdida de conexión, volvemos a autorizar
            try:
                client = get_sheet_client()
            except Exception:
                pass

        # Esperar 10 segundos antes del siguiente escaneo
        time.sleep(10)

if __name__ == "__main__":
    main_loop()
