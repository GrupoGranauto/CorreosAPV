/**
 * =========================================================================
 * SCRIPT UNIFICADO — Webhook Kommo + Invitaciones de Calendario APV (Versión Unificada _U)
 * =========================================================================
 * 
 * FLUJO COMPLETO:
 *   1. Kommo envía webhook → doPost() escribe en hoja "Citados"
 *   2. ARRAYFORMULA en "Calendar" mapea los datos automáticamente
 *   3. doPost() llama a _escanearCalendar_U() inmediatamente después
 *   4. _escanearCalendar_U() detecta la fila nueva, marca checkbox,
 *      gestiona estados (Esperando → Procesando → Enviado/Error)
 *      y envía la invitación de calendario al APV vía Railway
 *
 * DESPLIEGUE:
 *   - Desplegar como Web App (doPost/doGet) en Apps Script
 *   - NO se necesitan triggers de tiempo ni onChange
 *   - El menú "📅 Citas BDC (Unificado)" permite procesar filas manualmente
 *
 * NOTA DE INTEGRACIÓN:
 *   Para evitar conflictos con otros scripts del mismo proyecto, todos los nombres
 *   de este script terminan en _U o _U(). Si tu otro script tiene su propia función
 *   onOpen(), puedes agregar el menú de este script llamando a onOpen_U() dentro de ella.
 * =========================================================================
 */


// ═════════════════════════════════════════════════════════════════════════
// SECCIÓN 1: CONFIGURACIÓN
// ═════════════════════════════════════════════════════════════════════════

// ── Webhook Kommo → Citados ──
const SHEET_CITADOS_U         = 'Citados';
const CITA_FIELD_ID_U         = 1895392;   // Campo checkbox "cita agendada" en Kommo
const KOMMO_SUBDOMAIN_U     = 'ventasdigitalesga';
const SECRET_TOKEN_U        = 'mi-token-secreto';
const RAILWAY_WEBHOOK_URL_U = 'https://webhook-seekop-production.up.railway.app/webhook/prospecto';

const COLUMNS_CITADOS_U = [
  'Fecha registro', 'ID lead', 'Nombre lead', 'Etapa (status_id)',
  'Pipeline ID', 'Responsable (user_id)', 'Presupuesto',
  'Fuente (utm_source / loss_reason)', 'Fecha creación lead',
  'URL en Kommo', 'BDC primer contacto', 'Agencia', 'Auto',
  'APV', 'Celular', 'Fecha cita'
];

const CF_BDC_U      = 1856134;
const CF_AGENCIA_U  = 1865916;
const CF_AUTO_U     = 1866774;
const CF_APV_U      = 1866776;
const CF_CELULAR_U  = 1895066;
const CF_FECHA_CITA_U = 1854482;

// ── Calendar → Backend Railway (invitaciones) ──
const SHEET_DESTINO_U = 'Calendar';
const RAILWAY_SERVICE_URL_U = "https://web-production-d986f9.up.railway.app";
const WEBHOOK_ENDPOINT_U = RAILWAY_SERVICE_URL_U + "/webhook";
const API_SECRET_KEY_U = "sk_apv_Xk9mP3nQ7rT1bV5zA2";

// ── Mapa de columnas de Calendar (1-indexed) ──
const COL_U = {
  APV: 1,        // A
  NOMBRE: 2,     // B
  APELLIDO: 3,   // C
  CONTACTO: 4,   // D
  UNIDAD: 5,     // E
  AGENCIA: 6,    // F
  // G = Agencia Sekoop (se ignora en el envío)
  BDC: 8,        // H
  DIA: 9,        // I
  HORA: 10,      // J
  EMAIL: 11,     // K
  CHECKBOX: 12,  // L — Casilla de verificación
  ESTADO: 13     // M — Estado del envío
};

// ── Estados y colores ──
const ESTADO_U = {
  ESPERANDO: 'Esperando...',
  PROCESANDO: 'Procesando...',
  ENVIADO: 'Enviado ✓',
  ERROR: 'ERROR'
};

const COLORES_U = {
  ESPERANDO: { bg: '#FFF3CD', texto: '#856404' },   // 🟡 Amarillo
  PROCESANDO: { bg: '#CCE5FF', texto: '#004085' },   // 🔵 Azul
  ENVIADO: { bg: '#D4EDDA', texto: '#155724' },      // 🟢 Verde
  ERROR: { bg: '#F8D7DA', texto: '#721C24' },         // 🔴 Rojo
  FILA_ENVIADA: '#EEEDFA'                             // 💜 Lila claro
};


// ═════════════════════════════════════════════════════════════════════════
// SECCIÓN 2: MENÚ PERSONALIZADO
// ═════════════════════════════════════════════════════════════════════════

function onOpen_U() {
  SpreadsheetApp.getUi()
    .createMenu('📅 Citas BDC (Unificado)')
    .addItem('Enviar Invitaciones Pendientes', 'enviarPendientes_U')
    .addItem('Escaneo Manual Completo', 'universalScanner_U')
    .addSeparator()
    .addItem('⚙️ Configurar Triggers (ejecutar 1 vez)', 'configurarTriggers_U')
    .addToUi();
}


// ═════════════════════════════════════════════════════════════════════════
// SECCIÓN 3: WEBHOOK DE KOMMO → HOJA "CITADOS"
// ═════════════════════════════════════════════════════════════════════════

/**
 * Recibe el webhook de Kommo. Escribe en "Citados" si el lead tiene
 * el checkbox "cita agendada" marcado. Después dispara el escáner
 * de Calendar para enviar la invitación inmediatamente.
 */
function doPost(e) {
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(30000);
  } catch (lockErr) {
    console.error('No se pudo obtener lock: ' + lockErr);
    return _json_U({ ok: false, error: 'busy, retry' });
  }

  try {
    // ── Validar token de seguridad ──
    if (SECRET_TOKEN_U) {
      const token = e.parameter && e.parameter.token;
      if (token !== SECRET_TOKEN_U) {
        return _json_U({ ok: false, error: 'invalid token' });
      }
    }

    const payload = _parsePayload_U(e);
    const leads   = _extractLeads_U(payload);
    if (!leads.length) {
      return _json_U({ ok: true, skipped: 'no leads in payload' });
    }

    const ss           = SpreadsheetApp.getActiveSpreadsheet();
    const sheetCitados = ss.getSheetByName(SHEET_CITADOS_U);
    const props        = PropertiesService.getScriptProperties();

    let written = 0;
    let skipped = 0;

    leads.forEach(function (lead) {
      const checked = _isCheckboxChecked_U(lead, CITA_FIELD_ID_U);
      if (!checked) { skipped++; return; }

      const propKey = 'cita_lead_' + lead.id;
      if (props.getProperty(propKey)) { skipped++; return; }

      if (_alreadyLogged_U(sheetCitados, lead.id)) {
        props.setProperty(propKey, '1');
        skipped++;
        return;
      }

      sheetCitados.appendRow(_buildRow_U(lead));
      SpreadsheetApp.flush();
      props.setProperty(propKey, '1');
      written++;
    });

    // ════════════════════════════════════════════════════════════════
    // CLAVE: Si se escribieron filas, escanear Calendar inmediatamente
    // para enviar las invitaciones sin esperar ningún trigger externo.
    // ════════════════════════════════════════════════════════════════
    var invitacionesEnviadas = 0;
    if (written > 0) {
      // Pausa breve para que ARRAYFORMULA recalcule en Calendar
      Utilities.sleep(2000);
      SpreadsheetApp.flush();

      try {
        invitacionesEnviadas = _escanearCalendar_U();
        console.log('Invitaciones enviadas tras webhook: ' + invitacionesEnviadas);
      } catch (scanErr) {
        console.error('Error en escáner Calendar post-webhook: ' + scanErr);
      }
    }

    return _json_U({
      ok: true,
      citados_written: written,
      citados_skipped: skipped,
      invitaciones_enviadas: invitacionesEnviadas
    });

  } catch (err) {
    console.error(err);
    return _json_U({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function doGet() {
  return _json_U({ ok: true, service: 'kommo-citados-calendar-unificado' });
}


// ═════════════════════════════════════════════════════════════════════════
// SECCIÓN 4: ESCÁNER DE CALENDAR → ENVÍO DE INVITACIONES
// ═════════════════════════════════════════════════════════════════════════

/**
 * Función pública con lock — para uso desde menú o triggers.
 * Internamente llama a _escanearCalendar_U().
 */
function universalScanner_U() {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(15000);
  } catch (e) {
    console.warn('universalScanner_U: No se pudo obtener lock.');
    return;
  }

  try {
    var procesadas = _escanearCalendar_U();
    if (procesadas > 0) {
      console.log('universalScanner_U: ' + procesadas + ' fila(s) procesada(s).');
    }
  } catch (err) {
    console.error('universalScanner_U error: ' + err);
  } finally {
    lock.releaseLock();
  }
}

/**
 * Función interna SIN lock — puede ser llamada desde doPost
 * (que ya tiene su propio lock) sin causar deadlock.
 *
 * @return {number} Cantidad de filas procesadas exitosamente.
 */
function _escanearCalendar_U() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_DESTINO_U);
  if (!sheet) return 0;

  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) return 0;

  // ── Lectura por lotes (una sola llamada a la API de Sheets) ──
  var numRows = lastRow - 1;
  var dataRange = sheet.getRange(2, 1, numRows, COL_U.ESTADO);
  var rawValues = dataRange.getValues();

  var procesadas = 0;

  for (var i = 0; i < rawValues.length; i++) {
    var fila = i + 2;

    var apv = rawValues[i][COL_U.APV - 1];
    var email = rawValues[i][COL_U.EMAIL - 1];
    var dia = rawValues[i][COL_U.DIA - 1];
    var hora = rawValues[i][COL_U.HORA - 1];
    var checkbox = rawValues[i][COL_U.CHECKBOX - 1];
    var estado = String(rawValues[i][COL_U.ESTADO - 1] || '').trim();

    // Saltar filas vacías
    if (!apv || String(apv).trim() === '') continue;

    // Saltar filas ya enviadas o en proceso
    if (estado.toUpperCase().indexOf('ENVIADO') !== -1) continue;
    if (estado === ESTADO_U.PROCESANDO) continue;

    // Campos obligatorios
    if (!email || !dia || !hora) continue;

    // CASO 1: Fila nueva (sin estado o con FALSE residual)
    var esNueva = (estado === '' || estado === 'false' || estado === 'FALSE');

    // CASO 2: Fila con error, usuario volvió a marcar checkbox
    var esReintento = (estado.toUpperCase().indexOf('ERROR') !== -1 && checkbox === true);

    if (esNueva || esReintento) {
      _procesarFilaNueva_U(sheet, fila);
      procesadas++;
    }
  }

  return procesadas;
}

/**
 * Envío masivo manual (desde menú).
 */
function enviarPendientes_U() {
  var lock = LockService.getScriptLock();
  try { lock.waitLock(15000); } catch (e) { return; }

  try {
    var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_DESTINO_U);
    if (!sheet) return;

    var lastRow = sheet.getLastRow();
    if (lastRow <= 1) {
      SpreadsheetApp.getUi().alert('No hay filas para procesar.');
      return;
    }

    var numRows = lastRow - 1;
    var rawValues = sheet.getRange(2, 1, numRows, COL_U.ESTADO).getValues();
    var enviadas = 0;
    var errores = 0;

    for (var i = 0; i < rawValues.length; i++) {
      var fila = i + 2;
      var apv = rawValues[i][COL_U.APV - 1];
      var checkbox = rawValues[i][COL_U.CHECKBOX - 1];
      var estado = String(rawValues[i][COL_U.ESTADO - 1] || '').trim().toUpperCase();

      if (!apv || String(apv).trim() === '') continue;
      if (checkbox !== true) continue;
      if (estado.indexOf('ENVIADO') !== -1) continue;

      _setEstado_U(sheet, fila, ESTADO_U.PROCESANDO, COLORES_U.PROCESANDO);
      SpreadsheetApp.flush();

      if (_enviarARailway_U(sheet, fila)) {
        enviadas++;
      } else {
        errores++;
      }
    }

    SpreadsheetApp.getUi().alert(
      '✅ Proceso terminado.\n\n' +
      '• Enviadas correctamente: ' + enviadas + '\n' +
      '• Con errores: ' + errores
    );

  } finally {
    lock.releaseLock();
  }
}


// ═════════════════════════════════════════════════════════════════════════
// SECCIÓN 5: FUNCIONES INTERNAS — CALENDAR
// ═════════════════════════════════════════════════════════════════════════

/**
 * Flujo completo de estados para una fila:
 *   Checkbox ✓ → Esperando... → Procesando... → Enviado ✓ / ERROR
 */
function _procesarFilaNueva_U(sheet, fila) {
  // Paso 1: Marcar checkbox + "Esperando..."
  sheet.getRange(fila, COL_U.CHECKBOX).setValue(true);
  _setEstado_U(sheet, fila, ESTADO_U.ESPERANDO, COLORES_U.ESPERANDO);
  SpreadsheetApp.flush();

  // Paso 2: "Procesando..."
  _setEstado_U(sheet, fila, ESTADO_U.PROCESANDO, COLORES_U.PROCESANDO);
  SpreadsheetApp.flush();

  // Paso 3: Enviar al backend
  _enviarARailway_U(sheet, fila);
}

/**
 * Aplica texto y colores a la celda de estado (col M).
 */
function _setEstado_U(sheet, fila, texto, colores) {
  var celda = sheet.getRange(fila, COL_U.ESTADO);
  celda.setValue(texto);
  celda.setBackground(colores.bg);
  celda.setFontColor(colores.texto);
  celda.setFontWeight('bold');
}

/**
 * Envía los datos de la fila al backend Railway.
 * Actualiza el estado según el resultado.
 * @return {boolean} true si éxito, false si error.
 */
function _enviarARailway_U(sheet, fila) {
  var valores = sheet.getRange(fila, 1, 1, COL_U.EMAIL).getDisplayValues()[0];

  var emailApv = valores[COL_U.EMAIL - 1];
  var dia = valores[COL_U.DIA - 1];
  var hora = valores[COL_U.HORA - 1];

  if (!emailApv || !dia || !hora) {
    _setEstado_U(sheet, fila, 'ERROR: Faltan datos (Email/Fecha/Hora)', COLORES_U.ERROR);
    sheet.getRange(fila, COL_U.CHECKBOX).setValue(false);
    return false;
  }

  var timezone = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone();
  var payload = {
    apv: valores[COL_U.APV - 1] || 'N/A',
    nombre: valores[COL_U.NOMBRE - 1] || 'N/A',
    apellido: valores[COL_U.APELLIDO - 1] || '',
    contacto_cliente: valores[COL_U.CONTACTO - 1] || '',
    unidad_de_interes: valores[COL_U.UNIDAD - 1] || '',
    agencia_de_cita: valores[COL_U.AGENCIA - 1] || '',
    bdc_responsable: valores[COL_U.BDC - 1] || '',
    dia_de_cita: dia,
    hora_de_cita: hora,
    email_apv: emailApv,
    timezone: timezone
  };

  var opciones = {
    method: 'post',
    contentType: 'application/json',
    headers: { 'X-API-Key': API_SECRET_KEY_U },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  try {
    var respuesta = UrlFetchApp.fetch(WEBHOOK_ENDPOINT_U, opciones);
    var codigo = respuesta.getResponseCode();

    if (codigo === 200 || codigo === 201) {
      var ahora = Utilities.formatDate(new Date(), timezone, 'dd/MM/yyyy HH:mm:ss');
      
      // Parsear respuesta para verificar si se requiere envío de fallback
      var responseObj = null;
      try {
        responseObj = JSON.parse(respuesta.getContentText());
      } catch (e) {
        console.error('Error parseando JSON de respuesta: ' + e);
      }

      if (responseObj && responseObj.fallback_to_client && responseObj.email_payload) {
        try {
          var ep = responseObj.email_payload;
          
          var attachmentBlob = Utilities.newBlob(
            Utilities.base64Decode(ep.ics_content_base64),
            'text/calendar',
            ep.ics_filename
          );
          
          var mailOptions = {
            htmlBody: ep.html_body,
            attachments: [attachmentBlob]
          };
          
          if (ep.logo_base64) {
            var logoBlob = Utilities.newBlob(
              Utilities.base64Decode(ep.logo_base64),
              'image/png',
              'logo.png'
            );
            mailOptions.inlineImages = {
              'logo@apv': logoBlob
            };
          }
          
          GmailApp.sendEmail(ep.to, ep.subject, ep.body_text, mailOptions);
          console.log('Correo enviado desde Apps Script (fallback) a: ' + ep.to);
          _setEstado_U(sheet, fila, ESTADO_U.ENVIADO + ' (G) — ' + ahora, COLORES_U.ENVIADO);
        } catch (sendErr) {
          console.error('Error al enviar correo de fallback: ' + sendErr);
          _setEstado_U(sheet, fila, 'ERROR: Falló envío fallback (' + sendErr.message + ')', COLORES_U.ERROR);
          sheet.getRange(fila, COL_U.CHECKBOX).setValue(false);
          return false;
        }
      } else {
        _setEstado_U(sheet, fila, ESTADO_U.ENVIADO + ' — ' + ahora, COLORES_U.ENVIADO);
      }

      sheet.getRange(fila, 1, 1, COL_U.EMAIL).setBackground(COLORES_U.FILA_ENVIADA);
      return true;
    } else {
      var detalle = '';
      try {
        detalle = JSON.parse(respuesta.getContentText()).detail || '';
      } catch (e) {
        detalle = respuesta.getContentText().substring(0, 120);
      }
      _setEstado_U(sheet, fila, ESTADO_U.ERROR + ' ' + codigo + ': ' + detalle, COLORES_U.ERROR);
      sheet.getRange(fila, COL_U.CHECKBOX).setValue(false);
      return false;
    }
  } catch (error) {
    _setEstado_U(sheet, fila, ESTADO_U.ERROR + ' RED: ' + error.message, COLORES_U.ERROR);
    sheet.getRange(fila, COL_U.CHECKBOX).setValue(false);
    return false;
  }
}


// ═════════════════════════════════════════════════════════════════════════
// SECCIÓN 6: FUNCIONES AUXILIARES — KOMMO / CITADOS
// ═════════════════════════════════════════════════════════════════════════

function _parsePayload_U(e) {
  if (e.postData && e.postData.type && e.postData.type.indexOf('json') !== -1) {
    return JSON.parse(e.postData.contents || '{}');
  }
  const flat   = e.parameter || {};
  const nested = {};
  Object.keys(flat).forEach(function (key) {
    _assignDeep_U(nested, key, flat[key]);
  });
  return nested;
}

function _assignDeep_U(obj, path, value) {
  const parts = path.replace(/\]/g, '').split('[').map(function (p) { return p.trim(); });
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const k = parts[i];
    if (!(k in cur) || typeof cur[k] !== 'object' || cur[k] === null) {
      cur[k] = isNaN(parts[i + 1]) ? {} : [];
    }
    cur = cur[k];
  }
  cur[parts[parts.length - 1]] = value;
}

function _extractLeads_U(payload) {
  const out  = [];
  const root = payload.leads || payload;
  if (Array.isArray(root)) return root;
  ['add', 'status', 'update', 'create', 'note'].forEach(function (k) {
    if (Array.isArray(root[k])) {
      out.push.apply(out, root[k]);
    } else if (root[k] && typeof root[k] === 'object') {
      Object.keys(root[k]).forEach(function (i) { out.push(root[k][i]); });
    }
  });
  return out;
}

function _isCheckboxChecked_U(lead, fieldId) {
  if (!fieldId) return false;
  const fields = lead.custom_fields || lead.custom_fields_values || [];
  const list   = Array.isArray(fields) ? fields : Object.values(fields);
  for (let i = 0; i < list.length; i++) {
    const f = list[i];
    if (!f) continue;
    if (String(f.id) !== String(fieldId)) continue;
    const values = f.values || (f.value !== undefined ? [{ value: f.value }] : []);
    const arr    = Array.isArray(values) ? values : Object.values(values);
    for (let j = 0; j < arr.length; j++) {
      const item = arr[j];
      if (item === null || item === undefined) continue;
      const v = (typeof item === 'object') ? (item.value !== undefined ? item.value : item) : item;
      if (v === true || v === 1 || v === '1' || v === 'true' || v === 'on') return true;
    }
  }
  return false;
}

function _alreadyLogged_U(sheet, leadId) {
  if (!leadId) return false;
  const last = sheet.getLastRow();
  if (last < 2) return false;
  const idColIndex = COLUMNS_CITADOS_U.indexOf('ID lead') + 1;
  const ids        = sheet.getRange(2, idColIndex, last - 1, 1).getValues();
  const target     = String(leadId).trim();
  for (let i = 0; i < ids.length; i++) {
    if (String(ids[i][0]).trim() === target) return true;
  }
  return false;
}

function _buildRow_U(lead) {
  const now = new Date();
  const url = KOMMO_SUBDOMAIN_U && lead.id
    ? 'https://' + KOMMO_SUBDOMAIN_U + '.kommo.com/leads/detail/' + lead.id
    : '';
  return [
    now,
    lead.id || '',
    lead.name || '',
    lead.status_id || '',
    lead.pipeline_id || '',
    lead.responsible_user_id || lead.main_user_id || '',
    lead.price || lead.sale || '',
    lead.utm_source || lead.loss_reason_name || '',
    lead.created_at ? new Date(Number(lead.created_at) * 1000) : '',
    url,
    _getCustomFieldValue_U(lead, CF_BDC_U),
    _getCustomFieldValue_U(lead, CF_AGENCIA_U),
    _getCustomFieldValue_U(lead, CF_AUTO_U),
    _getCustomFieldValue_U(lead, CF_APV_U),
    _getCustomFieldValue_U(lead, CF_CELULAR_U),
    _getDateTimeFieldValue_U(lead, CF_FECHA_CITA_U)
  ];
}

/**
 * Helper to obtain custom field values.
 */
function _getCustomFieldValue_U(lead, fieldId) {
  if (!fieldId) return '';
  const fields = lead.custom_fields || lead.custom_fields_values || [];
  const list   = Array.isArray(fields) ? fields : Object.values(fields);
  for (let i = 0; i < list.length; i++) {
    const f = list[i];
    if (!f) continue;
    if (String(f.id) !== String(fieldId)) continue;
    const values = f.values || (f.value !== undefined ? [{ value: f.value }] : []);
    const arr    = Array.isArray(values) ? values : Object.values(values);
    const out    = [];
    for (let j = 0; j < arr.length; j++) {
      const v    = arr[j];
      if (v === null || v === undefined) continue;
      const text = (typeof v === 'object') ? (v.value !== undefined ? v.value : '') : v;
      if (text !== '' && text !== null && text !== undefined) out.push(text);
    }
    return out.join(', ');
  }
  return '';
}

/**
 * Helper to obtain date-time values.
 */
function _getDateTimeFieldValue_U(lead, fieldId) {
  if (!fieldId) return '';
  const fields = lead.custom_fields || lead.custom_fields_values || [];
  const list   = Array.isArray(fields) ? fields : Object.values(fields);
  for (let i = 0; i < list.length; i++) {
    const f = list[i];
    if (!f || String(f.id) !== String(fieldId)) continue;
    const values = f.values || (f.value !== undefined ? [f.value] : []);
    const arr    = Array.isArray(values) ? values : Object.values(values);
    if (!arr.length) return '';
    const raw = (typeof arr[0] === 'object' && arr[0] !== null) ? arr[0].value : arr[0];
    const ts  = parseInt(raw, 10);
    if (isNaN(ts)) return '';
    return new Date(ts * 1000);
  }
  return '';
}

function _json_U(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}


// ═════════════════════════════════════════════════════════════════════════
// SECCIÓN 7: TRIGGER MANUAL — CLIC EN CHECKBOX (onEdit instalable)
// ═════════════════════════════════════════════════════════════════════════

/**
 * Se dispara cuando el usuario edita cualquier celda.
 * Solo actúa cuando se marca el checkbox (col L) en la hoja Calendar.
 */
function onCheckboxEdit_U(e) {
  if (!e || !e.range) return;

  var sheet = e.range.getSheet();
  if (sheet.getName() !== SHEET_DESTINO_U) return;

  // Solo reaccionar a la columna L (checkbox)
  var col = e.range.getColumn();
  var row = e.range.getRow();
  if (col !== COL_U.CHECKBOX || row < 2) return;

  // Solo cuando se MARCA (true), no cuando se desmarca
  var valor = e.range.getValue();
  if (valor !== true) return;

  // No reprocesar filas ya enviadas
  var estado = String(sheet.getRange(row, COL_U.ESTADO).getValue() || '').trim();
  if (estado.toUpperCase().indexOf('ENVIADO') !== -1) return;
  if (estado === ESTADO_U.PROCESANDO) return;

  // Verificar que la fila tiene los datos necesarios
  var email = sheet.getRange(row, COL_U.EMAIL).getValue();
  var dia   = sheet.getRange(row, COL_U.DIA).getValue();
  var hora  = sheet.getRange(row, COL_U.HORA).getValue();

  if (!email || !dia || !hora) {
    _setEstado_U(sheet, row, 'ERROR: Faltan datos (Email/Fecha/Hora)', COLORES_U.ERROR);
    sheet.getRange(row, COL_U.CHECKBOX).setValue(false);
    return;
  }

  // Ejecutar el flujo completo de estados
  _setEstado_U(sheet, row, ESTADO_U.ESPERANDO, COLORES_U.ESPERANDO);
  SpreadsheetApp.flush();

  _setEstado_U(sheet, row, ESTADO_U.PROCESANDO, COLORES_U.PROCESANDO);
  SpreadsheetApp.flush();

  _enviarARailway_U(sheet, row);
}


// ═════════════════════════════════════════════════════════════════════════
// SECCIÓN 8: CONFIGURACIÓN DE TRIGGERS
// ═════════════════════════════════════════════════════════════════════════

/**
 * Ejecutar UNA VEZ desde el editor o desde el menú.
 *
 * 1. Elimina todos los triggers que apunten a 'onCheckboxEdit' o 'onCheckboxEdit_U'
 * 2. Crea un trigger onEdit instalable para onCheckboxEdit_U
 */
function configurarTriggers_U() {
  var triggers = ScriptApp.getProjectTriggers();
  var eliminados = 0;
  for (var i = 0; i < triggers.length; i++) {
    var handler = triggers[i].getHandlerFunction();
    if (handler === 'onCheckboxEdit' || handler === 'onCheckboxEdit_U') {
      ScriptApp.deleteTrigger(triggers[i]);
      eliminados++;
    }
  }
  console.log('Eliminados ' + eliminados + ' trigger(s) anteriores de checkbox.');

  // Crear trigger onEdit instalable → onCheckboxEdit_U
  ScriptApp.newTrigger('onCheckboxEdit_U')
    .forSpreadsheet(SpreadsheetApp.getActiveSpreadsheet())
    .onEdit()
    .create();
  console.log('Trigger creado: onCheckboxEdit_U (onEdit instalable).');

  SpreadsheetApp.getUi().alert(
    '✅ Triggers de este script configurados correctamente.\n\n' +
    '• Creado: onCheckboxEdit_U (detecta clic en checkbox)\n\n' +
    'El envío automático funciona via doPost (webhook de Kommo).\n' +
    'El envío manual funciona al marcar el checkbox en columna L.'
  );
}
