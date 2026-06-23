# 📅 Servicio de Invitaciones de Calendario APV

Este servicio automatiza el envío de invitaciones de Gmail (eventos de Google Calendar) a los Asesores Profesionales de Ventas (APV) cuando se registra una nueva cita en la pestaña **"Calendar"** de Google Sheets.

El sistema consta de dos partes:
1. **Backend en Railway (Python/FastAPI):** Recibe los datos de la cita, crea una invitación interactiva estándar de iCalendar (`.ics`) y envía un correo estructurado por SMTP con la invitación adjunta e incrustada.
2. **Script de Google Sheets (Google Apps Script):** Detecta cuándo se completa una fila en Google Sheets, envía los datos al backend y marca la fila como `ENVIADO` para evitar duplicados.

---

## 🛠️ Paso 1: Configurar tu Cuenta de Gmail (SMTP)

Para que el servidor pueda enviar correos en tu nombre de manera segura, debes usar una **Contraseña de Aplicación** de Google (no tu contraseña normal).

1. Ve a la configuración de tu cuenta de Google: [Mi Cuenta de Google](https://myaccount.google.com/).
2. En el menú de la izquierda, selecciona **Seguridad**.
3. Asegúrate de tener activada la **Verificación en dos pasos**. Si no, actívala.
4. Busca la sección **Contraseñas de aplicaciones** (puedes buscarlo directamente en la barra de búsqueda superior).
5. Escribe un nombre para identificar la aplicación (ejemplo: `Servicio Citas Railway`).
6. Haz clic en **Crear**.
7. Google te mostrará un código de **16 caracteres** dentro de un recuadro amarillo. **Copia esta contraseña** (sin los espacios). La usaremos como tu contraseña SMTP en las variables de entorno.

---

## 🚀 Paso 2: Despliegue del Servicio en Railway

El proyecto está preparado para que Railway lo detecte y despliegue automáticamente sin necesidad de Dockerfile (Railway detecta Python mediante el archivo `requirements.txt`).

1. Sube este repositorio a tu cuenta de **GitHub**.
2. Entra a [Railway.app](https://railway.app/) e inicia sesión.
3. Haz clic en **New Project** y selecciona **Deploy from GitHub repo**.
4. Elige el repositorio donde subiste este código.
5. Antes de desplegar, ve a la pestaña **Variables** del servicio en Railway y añade las siguientes variables de entorno:

| Variable | Valor | Descripción |
| :--- | :--- | :--- |
| `SMTP_USER` | `tu_correo@gmail.com` | Tu dirección de correo de Gmail remitente. |
| `SMTP_PASS` | `xxxx xxxx xxxx xxxx` | La contraseña de aplicación de 16 caracteres que creaste en el Paso 1. |
| `SMTP_HOST` | `smtp.gmail.com` | Host de Gmail SMTP (por defecto). |
| `SMTP_PORT` | `587` | Puerto SMTP TLS (por defecto). |
| `DEFAULT_TIMEZONE` | `America/Mexico_City` | Zona horaria para la interpretación de las fechas (ej. `America/Mexico_City`). |

6. Una vez configuradas las variables, Railway compilará y desplegará la aplicación.
7. Ve a la pestaña **Settings** en Railway y haz clic en **Generate Domain** en la sección *Networking*.
8. Copia la URL pública generada (ej. `https://apv-calendars-production.up.railway.app`). La usaremos en Google Sheets.

---

## 📊 Paso 3: Configurar Google Sheets (Google Apps Script)

### 1. Copiar el Script
1. Abre tu hoja de Google Sheets: [Google Sheets](https://docs.google.com/spreadsheets/d/1o_gr48pIWG5hrVf51Q5R8RPTa4eJL6f6a0Whk-2LGN0/edit#gid=465778881).
2. En el menú superior, ve a **Extensiones** > **Apps Script**.
3. Borra el código existente en el archivo `Código.gs` (o crea uno nuevo) y pega todo el contenido del archivo [appsscript.js](file:///c:/Users/famil/OneDrive/Escritorio/APV_Calendars/appsscript.js).
4. En la línea 8 del código que pegaste, reemplaza el valor de `RAILWAY_SERVICE_URL` por la URL pública generada por Railway en el Paso 2:
   ```javascript
   const RAILWAY_SERVICE_URL = "https://tu-servicio-railway.up.railway.app";
   ```
5. Guarda el proyecto haciendo clic en el icono del disco (Guardar proyecto).

### 2. Configurar el Disparador Instalable (Trigger)
Los disparadores simples integrados (como escribir `onEdit` tal cual) no tienen permisos para enviar datos fuera de Google. Por lo tanto, debemos configurar un disparador instalable:

1. En el panel izquierdo de Apps Script, haz clic en el icono de reloj (**Activadores**).
2. Haz clic en el botón inferior derecho **+ Añadir activador**.
3. Configura el formulario de la siguiente manera:
   - **Selecciona qué función ejecutar:** `onCalendarEdit`
   - **Selecciona qué despliegue debe ejecutarse:** `Principal`
   - **Selecciona la fuente del evento:** `De la hoja de cálculo`
   - **Selecciona el tipo de evento:** `Al editar`
4. Haz clic en **Guardar**.
5. Google te pedirá otorgar permisos de red y de acceso al documento. Haz clic en tu cuenta, luego en **Configuración Avanzada** > **Ir a Proyecto sin título (no seguro)** y confirma los permisos.

---

## 📋 Estructura de Columnas Requerida en "Calendar"

Asegúrate de que la hoja llamada exactamente **`Calendar`** tenga estas columnas en las primeras 12 columnas (de la **A** a la **L**):

* **A**: `APV` (Nombre del Asesor)
* **B**: `Nombre` (Nombre del Cliente)
* **C**: `Apellido` (Apellido del Cliente)
* **D**: `Contacto cliente` (Teléfono del Cliente)
* **E**: `Unidad de interés` (Auto)
* **F**: `Agencia de cita` (Ubicación física)
* **G**: `Agencia Sekoop` *(Esta columna se ignora al enviar la invitación)*
* **H**: `BDC Responsable` (Agente del BDC)
* **I**: `Día de cita` (Fecha en formato YYYY-MM-DD o DD/MM/YYYY)
* **J**: `Hora de Cita` (Hora en formato HH:MM)
* **K**: `Email APV` (Correo del asesor que recibirá la cita)
* **L**: `Invitación Estado` *(Esta columna la llena el script automáticamente como `ENVIADO - Fecha/Hora` tras enviar con éxito la invitación)*

---

## ⚡ Modo de Uso

### Envío Automático
Cuando rellenes una nueva fila en Google Sheets, en cuanto escribas la fecha, la hora y el correo en la columna K (`Email APV`), el disparador se activará automáticamente, enviará los datos a Railway, el backend enviará el correo con la invitación y escribirá `ENVIADO` en la columna L.

### Envío Manual / Masivo
Si por algún motivo la fila no se envió automáticamente (ej. copiaste y pegaste múltiples filas a la vez):
1. Recarga tu hoja de Sheets. Verás un nuevo menú en la parte superior derecha llamado `📅 Citas BDC`.
2. Haz clic en `📅 Citas BDC` > **Enviar Invitaciones Pendientes**.
3. El script escaneará toda la hoja y procesará todas las filas que tengan datos completos pero cuya columna L esté vacía. Al finalizar, te mostrará un mensaje confirmando cuántas invitaciones se enviaron.
