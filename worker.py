"""
Worker de Citas BDC: revisa la hoja "Calendar" y manda las invitaciones.

Una fila se procesa cuando la casilla "Enviar" (L) está marcada y el
"Estado Envío" (M) no empieza con "Enviado". Corre dentro de main.py como
hilo en segundo plano, o solo con: python worker.py
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()  # para correr local; en Railway las variables ya vienen del entorno

from mailer import GmailSender, build_invite, get_timezone

logger = logging.getLogger("apv_calendars.worker")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1o_gr48pIWG5hrVf51Q5R8RPTa4eJL6f6a0Whk-2LGN0")
SHEET_NAME = os.getenv("SHEET_NAME", "Calendar")
INTERVAL_SECONDS = int(os.getenv("WORKER_INTERVAL_SECONDS", "15"))
# Las horas de la hoja se interpretan en la zona horaria de la propia hoja
# (Archivo > Configuración). DEFAULT_TIMEZONE solo aplica si no se puede leer.
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/Mexico_City")
sheet_timezone = DEFAULT_TIMEZONE

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

# Columnas de "Calendar" (1-indexed)
COL_ENVIAR = 12   # L
COL_ESTADO = 13   # M

# Estado visible en /health
status = {
    "running": False,
    "disabled_reason": None,
    "timezone": None,
    "last_scan_at": None,
    "last_error": None,
    "sent": 0,
    "skipped_past": 0,
    "errors": 0,
}


def _now_local() -> datetime:
    return datetime.now(get_timezone(sheet_timezone))


def load_service_account_info():
    """Credenciales de la cuenta de servicio (Sheets) desde el entorno."""
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        return json.loads(raw)
    path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def config_problem():
    """Regresa por qué el worker no puede arrancar, o None si está listo."""
    try:
        if load_service_account_info() is None:
            return "Falta GOOGLE_SERVICE_ACCOUNT_JSON (cuenta de servicio para Sheets)"
    except ValueError as err:
        return f"GOOGLE_SERVICE_ACCOUNT_JSON no es JSON válido: {err}"
    if GmailSender.from_env() is None:
        return "Faltan GMAIL_SENDER / GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN"
    return None


def get_worksheet():
    global sheet_timezone
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        load_service_account_info(),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    spreadsheet = gspread.authorize(creds).open_by_key(SPREADSHEET_ID)
    sheet_timezone = spreadsheet.timezone or DEFAULT_TIMEZONE
    status["timezone"] = sheet_timezone
    return spreadsheet.worksheet(SHEET_NAME)


def row_to_cita(row: list) -> dict:
    row = [c.strip() for c in row] + [""] * 13
    return {
        "apv": row[0],
        "nombre": row[1],
        "apellido": row[2],
        "contacto_cliente": row[3],
        "unidad_de_interes": row[4],
        "agencia_de_cita": row[5],
        # row[6] = Agencia Sekoop (no se usa en la invitación)
        "bdc_responsable": row[7],
        "dia_de_cita": row[8],
        "hora_de_cita": row[9],
        "email_apv": row[10],
    }


def is_pending(row: list) -> bool:
    row = row + [""] * 13
    enviar = row[COL_ENVIAR - 1].strip().upper()
    estado = row[COL_ESTADO - 1].strip()
    return enviar in ("TRUE", "VERDADERO", "1") and not estado.startswith("Enviado")


def process_row(ws, row_idx: int, row: list, sender: GmailSender) -> str:
    """Procesa una fila. Regresa 'sent', 'past' o 'error'."""
    cita = row_to_cita(row)
    logger.info(f"Procesando fila {row_idx} — APV: {cita['apv']}")
    try:
        if not cita["email_apv"] or not cita["dia_de_cita"] or not cita["hora_de_cita"]:
            raise ValueError("Faltan datos obligatorios (Email APV, Día o Hora de cita)")
        if not EMAIL_REGEX.match(cita["email_apv"]):
            raise ValueError(f"El email del APV '{cita['email_apv']}' no tiene un formato válido.")

        msg, local_dt, _ = build_invite(cita, sender.sender, sheet_timezone)

        # No mandar invitaciones de citas que ya pasaron
        if local_dt < _now_local():
            ws.update_cell(row_idx, COL_ESTADO, "Omitido — la cita ya pasó")
            ws.update_cell(row_idx, COL_ENVIAR, False)
            logger.info(f"Fila {row_idx} omitida: cita pasada ({local_dt:%d/%m/%Y %H:%M})")
            return "past"

        ws.update_cell(row_idx, COL_ESTADO, "Procesando...")
        sender.send(msg)
        ws.update_cell(row_idx, COL_ESTADO, f"Enviado ✓ — {_now_local():%d/%m/%Y %H:%M:%S}")
        logger.info(f"Fila {row_idx} marcada como Enviado")
        return "sent"

    except Exception as err:
        logger.error(f"Error procesando fila {row_idx}: {err}", exc_info=True)
        try:
            ws.update_cell(row_idx, COL_ESTADO, f"ERROR: {str(err)[:100]}")
            # Desmarcar casilla para no reintentar infinitamente
            ws.update_cell(row_idx, COL_ENVIAR, False)
        except Exception as sheet_err:
            logger.error(f"No se pudo escribir el error en la fila {row_idx}: {sheet_err}")
        return "error"


def scan_once(ws, sender: GmailSender) -> int:
    """Revisa la hoja una vez. Regresa cuántas filas procesó."""
    values = ws.get_all_values()
    processed = 0
    for idx, row in enumerate(values[1:], start=2):
        if not is_pending(row):
            continue
        result = process_row(ws, idx, row, sender)
        processed += 1
        if result == "sent":
            status["sent"] += 1
        elif result == "past":
            status["skipped_past"] += 1
        else:
            status["errors"] += 1
        time.sleep(1)
    return processed


def run_forever(stop_event: threading.Event = None):
    stop_event = stop_event or threading.Event()
    problem = config_problem()
    if problem:
        status["disabled_reason"] = problem
        logger.warning(f"Worker desactivado: {problem}")
        return

    sender = GmailSender.from_env()
    ws = None
    status["running"] = True
    logger.info(f"Worker iniciado — hoja '{SHEET_NAME}', cada {INTERVAL_SECONDS}s, remitente {sender.sender}")

    while not stop_event.is_set():
        try:
            if ws is None:
                ws = get_worksheet()
            scan_once(ws, sender)
            status["last_scan_at"] = _now_local().isoformat(timespec="seconds")
            status["last_error"] = None
        except Exception as err:
            logger.error(f"Error en el ciclo del worker: {err}", exc_info=True)
            status["last_error"] = f"{type(err).__name__}: {err}"[:300]
            ws = None  # reconectar en el siguiente ciclo
        stop_event.wait(INTERVAL_SECONDS)

    status["running"] = False


def start_in_background() -> threading.Event:
    """Arranca el worker en un hilo daemon. Regresa el evento para detenerlo."""
    stop_event = threading.Event()
    threading.Thread(target=run_forever, args=(stop_event,), name="sheet-worker", daemon=True).start()
    return stop_event


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    run_forever()
