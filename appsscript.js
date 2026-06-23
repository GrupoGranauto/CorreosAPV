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
const SHEET_ORIGEN = 'Prospectos'; // <-- CAMBIA ESTO por el nombre de la hoja donde caen los datos crudos
const SHEET_DESTINO = 'Calendar';

const COL_ESTADO = 12; // Columna L
const COL_CHECKBOX = 13; // Columna M (Agrega casillas de verificación en esta columna)

function onOpen() {
  var ui = SpreadsheetApp.getUi();
  ui.createMenu('📅 Citas BDC')
      .addItem('Enviar Invitaciones Pendientes', 'sendPendingInvites')
      .addToUi();
}

/**
 * 1. DISPARADOR AL EDITAR (onEdit instalable)
 * Maneja tanto la inserción de datos en la hoja origen como las marcas manuales en 'Calendar'
 */
function onCalendarEdit(e) {
  if (!e || !e.range) return;
  var range = e.range;
  var sheet = range.getSheet();
  var sheetName = sheet.getName();
  
  // CASO A: Caen datos en la hoja de origen (Ej. alguien o algo escribe la nueva fila)
  if (sheetName === SHEET_ORIGEN) {
    const startRow = range.getRow();
    const numRows = range.getNumRows();
    const sheetDestino = e.source.getSheetByName(SHEET_DESTINO);
    
    if (!sheetDestino) return;

    for (let i = 0; i < numRows; i++) {
      const row = startRow + i;
      // Ignorar cabecera
      if (row === 1) continue;
      
      // Auto-marcar la casilla en la hoja Calendar (Columna M = 13)
      sheetDestino.getRange(row, COL_CHECKBOX).setValue(true);
      sheetDestino.getRange(row, COL_ESTADO).setValue("PROCESANDO...");
      
      // Ejecutar envío
      processRow(sheetDestino, row);
    }
    return;
  }
  
  // CASO B: El usuario marca manualmente la casilla en la hoja "Calendar"
  if (sheetName === SHEET_DESTINO) {
    var rowNum = range.getRow();
    var colNum = range.getColumn();
    
    // Ignorar cabecera
    if (rowNum === 1) return;
    
    // Si el usuario marcó la casilla en la columna M (13)
    if (colNum === COL_CHECKBOX) {
      var val = range.getValue().toString().toUpperCase();
      if (val === "TRUE" || val === "VERDADERO") {
        sheet.getRange(rowNum, COL_ESTADO).setValue("PROCESANDO...");
        processRow(sheet, rowNum);
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
  var estado = values[11];      // L: Estado
  
  // Evitar duplicados
  if (estado.toString().toUpperCase().indexOf("ENVIADO") > -1) return null;
  
  if (!emailApv || !dia || !hora || !apv || !nombre) {
    sheet.getRange(rowNum, COL_ESTADO).setValue("ERROR: Faltan datos (Fecha/Hora/Email)");
    sheet.getRange(rowNum, COL_CHECKBOX).setValue(false); // Desmarcar casilla
    return false;
  }
  
  var timezone = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone();
  var payload = {
    "apv": apv, "nombre": nombre, "apellido": apellido, "contacto_cliente": contacto,
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
