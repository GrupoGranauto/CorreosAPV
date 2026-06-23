/**
 * =========================================================================
 * CONFIGURACIÓN DEL SCRIPT
 * =========================================================================
 * Reemplaza esta URL con la dirección de tu servicio en Railway una vez desplegado.
 */
const RAILWAY_SERVICE_URL = "https://web-production-d986f9.up.railway.app";
const WEBHOOK_ENDPOINT = RAILWAY_SERVICE_URL + "/webhook";
const API_SECRET_KEY = "sk_apv_Xk9mP3nQ7rT1bV5zA2";

// Configuración de Hojas y Columnas
const SHEET_ORIGEN = 'Citados'; // <-- Hoja donde caen los datos crudos
const SHEET_DESTINO = 'Calendar'; 

const COL_CHECKBOX = 12; // Columna L (Tus casillas están aquí)
const COL_ESTADO = 13;   // Columna M (Aquí escribirá "ENVIADO")

function onOpen() {
  var ui = SpreadsheetApp.getUi();
  ui.createMenu('📅 Citas BDC')
    .addItem('Procesar Todo Ahora (Prueba Manual)', 'universalScanner')
    .addToUi();
}

/**
 * 1. ESCÁNER AUTOMÁTICO UNIVERSAL (onChange instalable)
 * Este escáner revisa TODA la hoja Calendar. No requiere parámetros, por lo que 
 * funciona perfecto con el disparador "Al producirse un cambio".
 * Cubre: 1) Fila nueva por fórmula, 2) Clic manual en casilla.
 */
function universalScanner() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_DESTINO);
  if (!sheet) return;

  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) return;

  for (var r = 2; r <= lastRow; r++) {
    var email = sheet.getRange(r, 11).getValue(); // Col K (Email APV)
    var estado = sheet.getRange(r, COL_ESTADO).getValue().toString().toUpperCase();
    var checkbox = sheet.getRange(r, COL_CHECKBOX).getValue(); // Col L (Casilla)

    // Si ya fue enviada o está en proceso, ignorar.
    if (estado.indexOf("ENVIADO") !== -1 || estado.indexOf("PROCESANDO") !== -1) {
      continue;
    }

    var dia = sheet.getRange(r, 9).getValue();
    var hora = sheet.getRange(r, 10).getValue();

    // Solo actuamos si la fila tiene la información indispensable
    if (email && email !== "" && dia && hora) {

      // CASO A: Fila nueva (No tiene casilla ni estado)
      var esFilaNueva = (checkbox !== true && estado === "");

      // CASO B: El usuario dio clic manual a la casilla (True) y no se ha enviado
      var clicManual = (checkbox === true && estado === "");

      if (esFilaNueva || clicManual) {
        // Auto-marcar la casilla (por si era fila nueva) y poner PROCESANDO
        sheet.getRange(r, COL_CHECKBOX).setValue(true);
        sheet.getRange(r, COL_ESTADO).setValue("PROCESANDO...");

        // Enviar webhook
        processRow(sheet, r);
      }
    }
  }
}

/**
 * 2. ENVÍO MASIVO (Por si quieres enviar varios pendientes a la vez)
 */
function sendPendingInvites() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Calendar");
  if (!sheet) return;

  var lastRow = sheet.getLastRow();
  var countSuccess = 0;

  for (var r = 2; r <= lastRow; r++) {
    var estado = sheet.getRange(r, COL_ESTADO).getValue().toString().toUpperCase();
    var checkbox = sheet.getRange(r, COL_CHECKBOX).getValue().toString().toUpperCase();

    // Si la casilla está marcada pero no dice ENVIADO
    if ((checkbox === "TRUE" || checkbox === "VERDADERO") && estado.indexOf("ENVIADO") === -1) {
      if (processRow(sheet, r)) countSuccess++;
    }
  }
  SpreadsheetApp.getUi().alert("Proceso terminado. Invitaciones enviadas: " + countSuccess);
}

/**
 * 3. PROCESAR LA FILA Y MANDAR A RAILWAY
 * (Esta es la función que puedes llamar desde tu doPost si insertas filas por API)
 */
function processRow(sheet, rowNum) {
  var range = sheet.getRange(rowNum, 1, 1, 13);
  var values = range.getDisplayValues()[0];

  var apv = values[0];          // A: APV
  var nombre = values[1];       // B: Nombre
  var apellido = values[2];     // C: Apellido
  var contacto = values[3];     // D: Contacto
  var unidad = values[4];       // E: Unidad
  var agencia = values[5];      // F: Agencia
  var bdc = values[7];          // H: BDC
  var dia = values[8];          // I: Día
  var hora = values[9];         // J: Hora
  var emailApv = values[10];    // K: Email
  var estado = values[12];      // M: Estado 

  // Evitar duplicados
  if (estado.toString().toUpperCase().indexOf("ENVIADO") > -1) return null;

  // Validación menos estricta
  if (!emailApv || !dia || !hora) {
    sheet.getRange(rowNum, COL_ESTADO).setValue("ERROR: Faltan datos (Fecha/Hora/Email)");
    sheet.getRange(rowNum, COL_CHECKBOX).setValue(false); // Desmarcar casilla
    return false;
  }

  var timezone = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone();
  var payload = {
    "apv": apv || "N/A", "nombre": nombre || "N/A", "apellido": apellido || "N/A", "contacto_cliente": contacto,
    "unidad_de_interes": unidad, "agencia_de_cita": agencia, "bdc_responsable": bdc,
    "dia_de_cita": dia, "hora_de_cita": hora, "email_apv": emailApv, "timezone": timezone
  };

  var options = {
    "method": "post",
    "contentType": "application/json",
    "headers": { "X-API-Key": API_SECRET_KEY },
    "payload": JSON.stringify(payload),
    "muteHttpExceptions": true
  };

  try {
    var response = UrlFetchApp.fetch(WEBHOOK_ENDPOINT, options);
    var code = response.getResponseCode();

    if (code === 200 || code === 201) {
      var now = Utilities.formatDate(new Date(), timezone, "dd/MM/yyyy HH:mm:ss");
      sheet.getRange(rowNum, COL_ESTADO).setValue("ENVIADO - " + now);
      sheet.getRange(rowNum, 1, 1, 13).setBackground('#EEEDFA'); // Pintar fila de moradito claro
      return true;
    } else {
      sheet.getRange(rowNum, COL_ESTADO).setValue("ERROR SERVIDOR: " + code);
      sheet.getRange(rowNum, COL_CHECKBOX).setValue(false);
      return false;
    }
  } catch (error) {
    sheet.getRange(rowNum, COL_ESTADO).setValue("ERROR DE RED");
    sheet.getRange(rowNum, COL_CHECKBOX).setValue(false);
    return false;
  }
}
