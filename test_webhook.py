"""
Script de prueba para enviar un webhook de invitación de calendario.
Ejecuta primero el servidor con: python main.py
Luego en otra terminal: python test_webhook.py
"""
import json
import sys
import time
import urllib.request
import urllib.error

# ── Configuración ──────────────────────────────────────
SERVER_URL = "http://127.0.0.1:8000"
API_KEY = "sk_apv_Xk9mP3nQ7rT1bV5zA2"


def test_webhook(email_destino: str = "mborbon@grupogranauto.mx"):
    url = f"{SERVER_URL}/webhook"

    payload = {
        "apv": "Miguel Borbón",
        "nombre": "Carlos",
        "apellido": "García López",
        "contacto_cliente": "6637289512",
        "unidad_de_interes": "New Versa 2026",
        "agencia_de_cita": "Nissauto",
        "bdc_responsable": "Sistema BDC Automatizado",
        "dia_de_cita": "2026-06-25",
        "hora_de_cita": "10:30",
        "email_apv": email_destino,
        "timezone": "America/Hermosillo",
    }

    req_data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=req_data,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
        },
        method="POST",
    )

    print(f"Enviando invitación de prueba a: {email_destino}")
    print(f"URL del servidor: {url}")
    print(f"Payload:\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n")

    try:
        with urllib.request.urlopen(req) as response:
            status_code = response.getcode()
            body = json.loads(response.read().decode("utf-8"))
            print(f"Respuesta del Servidor (Código {status_code}):")
            print(json.dumps(body, indent=2, ensure_ascii=False))
            if body.get("success"):
                print("\n¡Invitación enviada con éxito!")
            if body.get("mock_mode"):
                print("(Modo simulación — no se envió correo real)")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"\nError HTTP {e.code}: {error_body}")
    except Exception as e:
        print(f"\nError de conexión: {e}")
        print("Asegúrate de que el servidor esté corriendo: python main.py")


if __name__ == "__main__":
    # Aceptar email como argumento de línea de comandos
    email = sys.argv[1] if len(sys.argv) > 1 else "mborbon@grupogranauto.mx"
    time.sleep(1)
    test_webhook(email)
