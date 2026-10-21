"""
Configura el envío por Gmail API y sube las credenciales a Railway.

Pasos (se pueden correr juntos o por separado):
  auth     Autoriza la cuenta remitente (abre el consentimiento de Google) y
           guarda el refresh token en ~/.apv_calendars/gmail_token.json
  prueba   Manda una invitación de prueba desde esta PC por Gmail API
  railway  Sube las variables a Railway (sin imprimir secretos) y redeploya

Uso:
  python configurar_railway.py auth --client client_secret_XXX.json
  python configurar_railway.py prueba --para correo@dominio.com
  python configurar_railway.py railway
  python configurar_railway.py todo --client client_secret_XXX.json --para correo@dominio.com

Requisitos: proyecto de Google Cloud con Gmail API habilitada y un cliente
OAuth tipo "App de escritorio"; Railway CLI con sesión iniciada.
"""
import argparse
import base64
import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

HOME_DIR = Path.home() / ".apv_calendars"
TOKEN_FILE = HOME_DIR / "gmail_token.json"
PROJECT_DIR = Path(__file__).parent
ENV_FILE = PROJECT_DIR / ".env"

DEFAULT_SERVICE_ACCOUNT = (
    r"G:\Unidades compartidas\BDC Grupo Granauto\BDC Business Intelligence"
    r"\credenciales\credencialesAI.json"
)
RAILWAY_PROJECT = "6f2d78be-23ff-4510-a065-723ff5e4de54"
RAILWAY_SERVICE = "apv-calendars"
RAILWAY_ENV = "production"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


def _email_from_id_token(id_token: str) -> str:
    payload = id_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")


def cmd_auth(args):
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_path = Path(args.client)
    if not client_path.exists():
        sys.exit(f"No existe el archivo del cliente OAuth: {client_path}")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), scopes=SCOPES)
    creds = flow.run_local_server(
        port=args.port,
        open_browser=not args.no_browser,
        authorization_prompt_message="Abre esta URL para autorizar: {url}",
        success_message="Listo. Ya puedes cerrar esta pestaña.",
        prompt="consent",
        login_hint=args.cuenta or None,
    )
    if not creds.refresh_token:
        sys.exit("Google no regresó refresh token. Revoca el acceso de la app y vuelve a correr 'auth'.")

    email = _email_from_id_token(creds.id_token) if getattr(creds, "id_token", None) else ""
    HOME_DIR.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "sender": email,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    }), encoding="utf-8")
    print(f"Autorizado como: {email or '(correo no disponible)'}")
    print(f"Token guardado en {TOKEN_FILE}")


def _load_token() -> dict:
    if not TOKEN_FILE.exists():
        sys.exit(f"No hay token. Corre primero: python {Path(__file__).name} auth --client <json>")
    return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))


def cmd_prueba(args):
    sys.path.insert(0, str(PROJECT_DIR))
    from mailer import GmailSender, build_invite

    tok = _load_token()
    sender = GmailSender(tok["sender"], tok["client_id"], tok["client_secret"], tok["refresh_token"])
    cita = {
        "apv": "Prueba Sistema BDC",
        "nombre": "Cliente",
        "apellido": "Prueba",
        "contacto_cliente": "0000000000",
        "unidad_de_interes": "Prueba de envío",
        "agencia_de_cita": "Prueba",
        "bdc_responsable": "Sistema BDC Automatizado",
        "dia_de_cita": args.dia,
        "hora_de_cita": args.hora,
        "email_apv": args.para,
    }
    msg, _, _ = build_invite(cita, tok["sender"], "America/Mexico_City")
    msg_id = sender.send(msg)
    print(f"Prueba enviada a {args.para} desde {tok['sender']} (gmail id {msg_id})")


def _railway_cmd():
    exe = shutil.which("railway")
    if not exe:
        sys.exit("No se encontró Railway CLI. Instálalo con: npm i -g @railway/cli")
    return [exe]


def _railway_set(key: str, value: str, skip_deploys: bool = True):
    """Guarda una variable en Railway leyendo el valor por stdin (no queda en la línea de comandos)."""
    cmd = _railway_cmd() + [
        "variable", "set", key, "--stdin",
        "-p", RAILWAY_PROJECT, "-s", RAILWAY_SERVICE, "-e", RAILWAY_ENV,
    ]
    if skip_deploys:
        cmd.append("--skip-deploys")
    result = subprocess.run(cmd, input=value, text=True, capture_output=True, cwd=PROJECT_DIR)
    if result.returncode != 0:
        sys.exit(f"Railway falló al guardar {key}: {result.stderr.strip() or result.stdout.strip()}")
    print(f"  ✓ {key}")


def _update_local_env(values: dict):
    """Agrega o reemplaza claves en el .env local (para pruebas con main.py / test_webhook.py)."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    pending = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)
    for key, value in pending.items():
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def cmd_railway(args):
    tok = _load_token()
    sa_path = Path(args.service_account)
    if not sa_path.exists():
        sys.exit(f"No existe la cuenta de servicio: {sa_path}")
    sa_json = json.dumps(json.loads(sa_path.read_text(encoding="utf-8")))
    api_key = secrets.token_urlsafe(32)

    print(f"Subiendo variables a Railway ({RAILWAY_SERVICE})...")
    _railway_set("GMAIL_CLIENT_ID", tok["client_id"])
    _railway_set("GMAIL_CLIENT_SECRET", tok["client_secret"])
    _railway_set("GMAIL_REFRESH_TOKEN", tok["refresh_token"])
    _railway_set("GOOGLE_SERVICE_ACCOUNT_JSON", sa_json)
    _railway_set("API_SECRET_KEY", api_key)
    _railway_set("GMAIL_SENDER", tok["sender"], skip_deploys=False)  # este dispara el redeploy

    _update_local_env({
        "GMAIL_SENDER": tok["sender"],
        "GMAIL_CLIENT_ID": tok["client_id"],
        "GMAIL_CLIENT_SECRET": tok["client_secret"],
        "GMAIL_REFRESH_TOKEN": tok["refresh_token"],
        "GOOGLE_SERVICE_ACCOUNT_FILE": str(sa_path),
        "API_SECRET_KEY": api_key,
    })
    print("Variables listas. Railway está redeployando el servicio.")
    print(f"API_SECRET_KEY nueva guardada también en {ENV_FILE.name} (no se imprime).")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paso", choices=["auth", "prueba", "railway", "todo"])
    parser.add_argument("--client", help="JSON del cliente OAuth (App de escritorio)")
    parser.add_argument("--cuenta", default="", help="Cuenta remitente sugerida en el consentimiento")
    parser.add_argument("--port", type=int, default=8765, help="Puerto local para el regreso de OAuth")
    parser.add_argument("--no-browser", action="store_true", help="No abrir el navegador; solo imprimir la URL")
    parser.add_argument("--para", default="", help="Destinatario de la invitación de prueba")
    parser.add_argument("--dia", default="2030-01-01", help="Día de la cita de prueba (YYYY-MM-DD)")
    parser.add_argument("--hora", default="10:00", help="Hora de la cita de prueba (HH:MM)")
    parser.add_argument("--service-account", default=DEFAULT_SERVICE_ACCOUNT, help="JSON de la cuenta de servicio de Sheets")
    args = parser.parse_args()

    if args.paso in ("auth", "todo") and not args.client:
        parser.error("--client es obligatorio para 'auth'")
    if args.paso == "prueba" and not args.para:
        parser.error("--para es obligatorio para 'prueba'")

    if args.paso in ("auth", "todo"):
        cmd_auth(args)
    if args.paso == "prueba" or (args.paso == "todo" and args.para):
        cmd_prueba(args)
    if args.paso in ("railway", "todo"):
        cmd_railway(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
