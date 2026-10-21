# 📅 Servicio de Invitaciones de Calendario APV

Envía una invitación de calendario (correo HTML + `.ics` con botones Sí/No/Quizás) al Asesor Profesional de Ventas (APV) cada vez que se agenda una cita en Kommo.

## Cómo funciona

```
Kommo (cita agendada)
   │  webhook
   ▼
Apps Script (appsscript.js) ──► hoja "Citados" (fila nueva)
                            └─► hoja "Calendar": casilla L = ✔, estado M = "Esperando..."
                                   ▲  ARRAYFORMULA sobre Citados (APV, correo, fecha, etc.)
                                   │
Railway: worker.py (cada 15 s) ────┘  lee filas con L = ✔ y M ≠ "Enviado"
   └─► Gmail API ──► correo al APV ──► M = "Enviado ✓ — fecha"
```

- **Remitente:** la cuenta autorizada por OAuth (hoy `bdccenter@grupogranauto.mx`).
- **Zona horaria:** el worker interpreta fecha/hora con la zona de la hoja (Archivo › Configuración; hoy `America/Hermosillo`).
- **Citas pasadas:** no se envían; se marcan `Omitido — la cita ya pasó` y se desmarca la casilla.
- **Errores:** se escriben en M como `ERROR: ...` y se desmarca la casilla. Para reintentar, corrige el dato y vuelve a marcar la casilla.
- **Por qué Gmail API y no SMTP:** Railway bloquea los puertos SMTP en planes sin costo; la Gmail API usa HTTPS.

## Archivos

| Archivo | Qué hace |
|---|---|
| `main.py` | FastAPI: arranca el worker en segundo plano, expone `/`, `/health` y `POST /webhook` (envío directo con `X-API-Key`). |
| `worker.py` | Lee la hoja "Calendar" y procesa las filas pendientes. También corre solo: `python worker.py`. |
| `mailer.py` | Arma el correo (texto, HTML con logo, `.ics`) y lo envía por Gmail API. |
| `configurar_railway.py` | Autoriza la cuenta remitente, manda prueba y sube credenciales a Railway sin imprimirlas. |
| `appsscript.js` | Receptor del webhook de Kommo (pegar en Extensiones › Apps Script de la hoja). |
| `test_webhook.py` | Prueba manual de `POST /webhook`. |

## Despliegue (Railway)

- Proyecto **APV Calendars**, servicio `apv-calendars`, conectado a `GrupoGranauto/CorreosAPV` rama `main` (cada push redeploya).
- URL: `https://apv-calendars-production.up.railway.app` — `/health` muestra el estado del worker.

### Variables

| Variable | Descripción |
|---|---|
| `GMAIL_SENDER` | Correo remitente (el que autorizó OAuth). |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` | Cliente OAuth "App de escritorio" (Google Cloud › proyecto `gasme-bi` › Google Auth Platform › Clientes › *APV Calendars Railway*). |
| `GMAIL_REFRESH_TOKEN` | Token de la cuenta remitente (scope `gmail.send`). |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON de la cuenta de servicio con edición en la hoja (`autoinsights@base-maestra-gn...`). |
| `API_SECRET_KEY` | Clave para `POST /webhook`. |
| `SPREADSHEET_ID`, `SHEET_NAME`, `WORKER_INTERVAL_SECONDS`, `WORKER_ENABLED`, `DEFAULT_TIMEZONE` | Opcionales (ver `.env.example`). |

### Reautorizar o cambiar remitente

```bash
python configurar_railway.py todo --client ruta/al/client_secret.json --cuenta remitente@grupogranauto.mx
```

Abre el consentimiento de Google, guarda el token en `~/.apv_calendars/`, sube todo a Railway y redeploya.

## Estructura de "Calendar"

A `APV` · B `Nombre` · C `Apellido` · D `Contacto cliente` · E `Unidad de interés` · F `Agencia de cita` · G `Agencia Sekoop` (no se usa) · H `BDC Responsable` · I `Día de cita` · J `Hora de Cita` · K `Email APV` · L `Enviar` (casilla) · M `Estado Envío`

A–K vienen del ARRAYFORMULA en `Calendar!A2` (el correo del APV sale de `Datos APV`, columna `CORREO`, buscando por `APV_KOMMO`). L y M las escriben Apps Script y el worker.

## Apps Script (Kommo → Sheets)

1. En la hoja: **Extensiones › Apps Script**, pega `appsscript.js`.
2. **Implementar › Administrar implementaciones › Editar › Nueva versión** (así la URL del webhook en Kommo no cambia).
3. El webhook de Kommo debe apuntar a la URL de la implementación con `?token=<SECRET_TOKEN_K>`.
