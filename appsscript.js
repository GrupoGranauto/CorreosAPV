/**
 * =========================================================================
 * SCRIPT WEBHOOK KOMMO — Solo Recepción y Marcado de Citas (Versión _K)
 * =========================================================================
 * 
 * Este script hace lo siguiente:
 *   1. Recibe el webhook de Kommo cuando se agenda una cita.
 *   2. Escribe los datos crudos en la hoja "Citados".
 *   3. Activa la casilla de verificación ("Enviar") de la nueva fila en la hoja
 *      "Calendar" (poblada mediante ARRAYFORMULA desde Citados) y establece el
 *      estado en "Esperando...".
 *   4. El script de Python local (sheet_worker.py) se encarga de detectar
 *      esta casilla marcada, enviar el correo y actualizar el estado a "Enviado".
 *
 * NOTA: Se eliminó toda la comunicación y dependencias hacia Railway,
 * simplificando al máximo el código y eliminando cualquier conflicto de nombres.
 * =========================================================================
 */

// ═════════════════════════════════════════════════════════════════════════
// CONFIGURACIÓN (Con sufijo _K para evitar colisiones)
// ═════════════════════════════════════════════════════════════════════════

const SHEET_CITADOS_K         = 'Citados';
const SHEET_DESTINO_K         = 'Calendar';
const CITA_FIELD_ID_K         = 1895392;   // Campo checkbox "cita agendada" en Kommo
const KOMMO_SUBDOMAIN_K       = 'ventasdigitalesga';
const SECRET_TOKEN_K          = 'mi-token-secreto';

const COLUMNS_CITADOS_K = [
  'Fecha registro', 'ID lead', 'Nombre lead', 'Etapa (status_id)',
  'Pipeline ID', 'Responsable (user_id)', 'Presupuesto',
  'Fuente (utm_source / loss_reason)', 'Fecha creación lead',
  'URL en Kommo', 'BDC primer contacto', 'Agencia', 'Auto',
  'APV', 'Celular', 'Fecha cita'
];

const CF_BDC_K      = 1856134;
const CF_AGENCIA_K  = 1865916;
const CF_AUTO_K     = 1866774;
const CF_APV_K      = 1866776;
const CF_CELULAR_K  = 1895066;
const CF_FECHA_CITA_K = 1854482;

// Mapa de columnas de Calendar (1-indexed)
const COL_K = {
  CHECKBOX: 12,  // L — Casilla de verificación
  ESTADO: 13     // M — Estado del envío
};

const ESTADO_K = {
  ESPERANDO: 'Esperando...'
};

const COLORES_K = {
  ESPERANDO: { bg: '#FFF3CD', texto: '#856404' }   // 🟡 Amarillo
};


// ═════════════════════════════════════════════════════════════════════════
// WEBHOOK DE KOMMO → HOJA "CITADOS"
// ═════════════════════════════════════════════════════════════════════════

function doPost(e) {
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(30000);
  } catch (lockErr) {
    console.error('No se pudo obtener lock: ' + lockErr);
    return _json_K({ ok: false, error: 'busy, retry' });
  }

  try {
    // ── Validar token de seguridad ──
    if (SECRET_TOKEN_K) {
      const token = e.parameter && e.parameter.token;
      if (token !== SECRET_TOKEN_K) {
        return _json_K({ ok: false, error: 'invalid token' });
      }
    }

    const payload = _parsePayload_K(e);
    const leads   = _extractLeads_K(payload);
    if (!leads.length) {
      return _json_K({ ok: true, skipped: 'no leads in payload' });
    }

    const ss           = SpreadsheetApp.getActiveSpreadsheet();
    const sheetCitados = ss.getSheetByName(SHEET_CITADOS_K);
    const props        = PropertiesService.getScriptProperties();

    let written = 0;
    let skipped = 0;

    leads.forEach(function (lead) {
      const checked = _isCheckboxChecked_K(lead, CITA_FIELD_ID_K);
      if (!checked) { skipped++; return; }

      const propKey = 'cita_lead_' + lead.id;
      if (props.getProperty(propKey)) { skipped++; return; }

      if (_alreadyLogged_K(sheetCitados, lead.id)) {
        props.setProperty(propKey, '1');
        skipped++;
        return;
      }

      sheetCitados.appendRow(_buildRow_K(lead));
      SpreadsheetApp.flush();
      props.setProperty(propKey, '1');
      written++;
    });

    // Si se escribieron filas en Citados, marcar la casilla en Calendar
    if (written > 0) {
      const sheetCalendar = ss.getSheetByName(SHEET_DESTINO_K);
      if (sheetCalendar) {
        // La fila en Calendar corresponde exactamente a la fila agregada en Citados
        var targetRow = sheetCitados.getLastRow();
        if (targetRow >= 2) {
          // Marcar checkbox "Enviar" a true
          sheetCalendar.getRange(targetRow, COL_K.CHECKBOX).setValue(true);
          
          // Establecer estado a "Esperando..." con color amarillo
          const celdaEstado = sheetCalendar.getRange(targetRow, COL_K.ESTADO);
          celdaEstado.setValue(ESTADO_K.ESPERANDO);
          celdaEstado.setBackground(COLORES_K.ESPERANDO.bg);
          celdaEstado.setFontColor(COLORES_K.ESPERANDO.texto);
          celdaEstado.setFontWeight('bold');
          
          SpreadsheetApp.flush();
        }
      }
    }

    return _json_K({
      ok: true,
      citados_written: written,
      citados_skipped: skipped
    });

  } catch (err) {
    console.error(err);
    return _json_K({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function doGet() {
  return _json_K({ ok: true, service: 'kommo-webhook-receptor-citas' });
}


// ═════════════════════════════════════════════════════════════════════════
// FUNCIONES AUXILIARES
// ═════════════════════════════════════════════════════════════════════════

function _parsePayload_K(e) {
  if (e.postData && e.postData.type && e.postData.type.indexOf('json') !== -1) {
    return JSON.parse(e.postData.contents || '{}');
  }
  const flat   = e.parameter || {};
  const nested = {};
  Object.keys(flat).forEach(function (key) {
    _assignDeep_K(nested, key, flat[key]);
  });
  return nested;
}

function _assignDeep_K(obj, path, value) {
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

function _extractLeads_K(payload) {
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

function _isCheckboxChecked_K(lead, fieldId) {
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

function _alreadyLogged_K(sheet, leadId) {
  if (!leadId) return false;
  const last = sheet.getLastRow();
  if (last < 2) return false;
  const idColIndex = COLUMNS_CITADOS_K.indexOf('ID lead') + 1;
  const ids        = sheet.getRange(2, idColIndex, last - 1, 1).getValues();
  const target     = String(leadId).trim();
  for (let i = 0; i < ids.length; i++) {
    if (String(ids[i][0]).trim() === target) return true;
  }
  return false;
}

function _buildRow_K(lead) {
  const now = new Date();
  const url = KOMMO_SUBDOMAIN_K && lead.id
    ? 'https://' + KOMMO_SUBDOMAIN_K + '.kommo.com/leads/detail/' + lead.id
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
    _getCustomFieldValue_K(lead, CF_BDC_K),
    _getCustomFieldValue_K(lead, CF_AGENCIA_K),
    _getCustomFieldValue_K(lead, CF_AUTO_K),
    _getCustomFieldValue_K(lead, CF_APV_K),
    _getCustomFieldValue_K(lead, CF_CELULAR_K),
    _getDateTimeFieldValue_K(lead, CF_FECHA_CITA_K)
  ];
}

function _getCustomFieldValue_K(lead, fieldId) {
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

function _getDateTimeFieldValue_K(lead, fieldId) {
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

function _json_K(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
