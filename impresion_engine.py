"""
impresion_engine.py — Blueprint del módulo Impresión de Arthemis Health.

Genera documentos HTML listos para impresión (print-friendly) o exportación
desde el navegador. No requiere dependencias de PDF externas.

Endpoints:
  GET /api/impresion/hc/<hc_id>            — documento completo de historia clínica
  GET /api/impresion/receta/<hc_id>        — receta médica
  GET /api/impresion/plan-cuidado/<hc_id>  — plan de cuidado al egreso
  GET /api/impresion/constancia/<admision_id> — constancia de atención
"""

import json
from datetime import datetime
from flask import Blueprint, request, jsonify, session, make_response

impresion_bp = Blueprint('impresion', __name__)


def _get_deps():
    import core
    return core


def _is_authenticated():
    return bool(session.get('user_id'))


# ── CSS base para documentos imprimibles ──────────────────────────────────────

_CSS_BASE = """
<style>
  @page { size: A4; margin: 15mm 20mm; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    font-size: 11pt; line-height: 1.5; color: #1a1a1a;
    max-width: 210mm; margin: 0 auto; padding: 15mm 20mm;
    background: #fff;
  }
  .header { display: flex; justify-content: space-between; align-items: flex-start;
             border-bottom: 2px solid #2c3e50; padding-bottom: 10px; margin-bottom: 15px; }
  .header-left { flex: 1; }
  .header-right { text-align: right; font-size: 9pt; color: #555; }
  .ips-name { font-size: 16pt; font-weight: 700; color: #2c3e50; margin-bottom: 2px; }
  .ips-detail { font-size: 9pt; color: #555; }
  .doc-title { text-align: center; font-size: 14pt; font-weight: 700; color: #2c3e50;
               margin: 15px 0 10px; text-transform: uppercase; letter-spacing: 1px; }
  .section { margin-bottom: 12px; }
  .section-title { font-size: 11pt; font-weight: 700; color: #2c3e50;
                   border-bottom: 1px solid #bdc3c7; padding-bottom: 3px; margin-bottom: 6px; }
  .field { display: inline-block; margin-right: 20px; margin-bottom: 4px; }
  .field-label { font-weight: 600; color: #555; font-size: 9pt; }
  .field-value { color: #1a1a1a; }
  .data-row { display: flex; flex-wrap: wrap; gap: 8px 20px; margin-bottom: 6px; }
  table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 10pt; }
  th { background: #ecf0f1; color: #2c3e50; font-weight: 600; text-align: left;
       padding: 6px 8px; border: 1px solid #bdc3c7; }
  td { padding: 5px 8px; border: 1px solid #ddd; vertical-align: top; }
  tr:nth-child(even) { background: #fafafa; }
  .signature-area { margin-top: 40px; display: flex; justify-content: space-between; }
  .signature-block { text-align: center; width: 45%; }
  .signature-line { border-top: 1px solid #333; margin-top: 50px; padding-top: 5px;
                    font-size: 10pt; }
  .footer { margin-top: 30px; padding-top: 10px; border-top: 1px solid #bdc3c7;
            font-size: 8pt; color: #777; text-align: center; }
  .text-block { white-space: pre-wrap; background: #f9f9f9; padding: 8px 10px;
                border-left: 3px solid #3498db; margin: 4px 0 8px; font-size: 10pt; }
  .print-btn { display: block; margin: 10px auto; padding: 10px 30px; font-size: 12pt;
               background: #2c3e50; color: #fff; border: none; border-radius: 4px;
               cursor: pointer; }
  .print-btn:hover { background: #34495e; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 3px;
           font-size: 9pt; font-weight: 600; }
  .badge-triage-I { background: #e74c3c; color: #fff; }
  .badge-triage-II { background: #e67e22; color: #fff; }
  .badge-triage-III { background: #f1c40f; color: #333; }
  .badge-triage-IV { background: #2ecc71; color: #fff; }
  .badge-triage-V { background: #3498db; color: #fff; }
  @media print {
    .print-btn { display: none !important; }
    body { padding: 0; margin: 0; }
    .header { page-break-inside: avoid; }
    .section { page-break-inside: avoid; }
  }
</style>
"""


def _html_doc(title, body_html, tenant=None):
    """Wrap body content in a full HTML document with print-friendly styles."""
    tenant = tenant or {}
    ips_name = tenant.get('nombre_clinica', 'IPS')
    nit = tenant.get('nit', '')
    direccion = tenant.get('direccion', '')
    ciudad = tenant.get('ciudad', '')
    telefono = tenant.get('telefono', '')

    header = f"""
    <div class="header">
      <div class="header-left">
        <div class="ips-name">{_esc(ips_name)}</div>
        <div class="ips-detail">NIT: {_esc(nit)}</div>
        <div class="ips-detail">{_esc(direccion)} - {_esc(ciudad)}</div>
        <div class="ips-detail">Tel: {_esc(telefono)}</div>
      </div>
      <div class="header-right">
        <div>Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
      </div>
    </div>
    <div class="doc-title">{_esc(title)}</div>
    """

    footer = f"""
    <div class="footer">
      {_esc(ips_name)} &bull; NIT {_esc(nit)} &bull; {_esc(direccion)}, {_esc(ciudad)}
      &bull; Documento generado el {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    </div>
    """

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_esc(title)} - {_esc(ips_name)}</title>
  {_CSS_BASE}
</head>
<body>
  <button class="print-btn" onclick="window.print()">Imprimir</button>
  {header}
  {body_html}
  {footer}
</body>
</html>"""


def _esc(val):
    """Escape HTML entities in a string value."""
    if val is None:
        return ''
    s = str(val)
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _field(label, value):
    """Render a single label-value field."""
    return f'<span class="field"><span class="field-label">{_esc(label)}:</span> <span class="field-value">{_esc(value)}</span></span>'


def _parse_json(val, default=None):
    """Safely parse a JSON string or return the value if already parsed."""
    if default is None:
        default = {}
    if isinstance(val, (dict, list)):
        return val
    if not val:
        return default
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


def _html_response(html):
    """Create an HTML response with proper content type."""
    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    return resp


# ── GET /api/impresion/hc/<hc_id> ─────────────────────────────────────────────

@impresion_bp.route('/api/impresion/hc/<int:hc_id>')
def impresion_hc(hc_id):
    """Generate full clinical history document as printable HTML."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        # HC
        hc = core.row(cur, core.adapt(
            "SELECT * FROM historia_clinica WHERE id=?", db), (hc_id,))
        if not hc:
            return jsonify({'error': 'Historia clínica no encontrada'}), 404

        admision_id = hc.get('admision_id')
        paciente_id = hc.get('paciente_id')

        # Patient
        paciente = core.row(cur, core.adapt(
            "SELECT * FROM pacientes WHERE id=?", db), (paciente_id,))

        # Admission
        adm = core.row(cur, core.adapt(
            "SELECT * FROM admisiones WHERE id=?", db), (admision_id,))

        # Triage
        triage = core.row(cur, core.adapt(
            "SELECT * FROM triage_clinico WHERE admision_id=?", db), (admision_id,))

        # Evoluciones
        evoluciones = core.rows(cur, core.adapt(
            "SELECT * FROM hc_evoluciones WHERE hc_id=? ORDER BY creado_en", db), (hc_id,))

        # Orders with results
        ordenes = core.rows(cur, core.adapt(
            "SELECT * FROM ordenes_medicas WHERE hc_id=? ORDER BY creado_en", db), (hc_id,))

        resultados_map = {}
        if ordenes:
            orden_ids = [str(o['id']) for o in ordenes]
            if orden_ids:
                placeholders = ','.join(['?' for _ in orden_ids])
                resultados = core.rows(cur, core.adapt(
                    f"SELECT * FROM orden_resultados WHERE orden_id IN ({placeholders}) "
                    f"ORDER BY creado_en", db), orden_ids)
                for r in resultados:
                    oid = r['orden_id']
                    resultados_map.setdefault(oid, []).append(r)

        # Prescriptions
        prescripciones = core.rows(cur, core.adapt(
            "SELECT * FROM prescripciones WHERE hc_id=? ORDER BY creado_en", db), (hc_id,))

        # Interconsultas
        interconsultas = core.rows(cur, core.adapt(
            "SELECT * FROM interconsultas WHERE hc_id=? ORDER BY creado_en", db), (hc_id,))

        # Tenant
        tenant = core.row(cur, core.adapt(
            "SELECT * FROM tenant_config WHERE tenant_id='default'", db)) or {}

        # ── Build HTML ────────────────────────────────────────────────────────

        body = ''

        # Patient info
        if paciente:
            nombre = f"{paciente.get('nombres', '')} {paciente.get('apellidos', '')}".strip()
            body += '<div class="section">'
            body += '<div class="section-title">Datos del Paciente</div>'
            body += '<div class="data-row">'
            body += _field('Nombre', nombre)
            body += _field('Documento', f"{paciente.get('tipo_doc', 'CC')} {paciente.get('num_doc', '')}")
            body += _field('Fecha Nacimiento', paciente.get('fecha_nacimiento', ''))
            body += _field('Genero', paciente.get('genero', ''))
            body += _field('EPS', paciente.get('eps', ''))
            body += '</div></div>'

        # Motivo de consulta
        body += '<div class="section">'
        body += '<div class="section-title">Motivo de Consulta</div>'
        body += f'<div class="text-block">{_esc(hc.get("motivo_consulta", ""))}</div>'
        body += '</div>'

        # Triage
        if triage:
            nivel = triage.get('nivel_asignado', '')
            body += '<div class="section">'
            body += f'<div class="section-title">Triage <span class="badge badge-triage-{_esc(nivel)}">Nivel {_esc(nivel)}</span></div>'
            body += '<div class="data-row">'
            body += _field('TA', f"{triage.get('ta_sistolica', '')}/{triage.get('ta_diastolica', '')} mmHg")
            body += _field('FC', f"{triage.get('fc', '')} lpm")
            body += _field('FR', f"{triage.get('fr', '')} rpm")
            body += _field('Temp', f"{triage.get('temperatura', '')} C")
            body += _field('SpO2', f"{triage.get('spo2', '')}%")
            body += _field('Glasgow', triage.get('glasgow_total', ''))
            body += _field('EVA Dolor', triage.get('eva_dolor', ''))
            body += '</div>'
            if triage.get('notas_enfermeria'):
                body += f'<div class="text-block">{_esc(triage["notas_enfermeria"])}</div>'
            body += '</div>'

        # Diagnoses
        body += '<div class="section">'
        body += '<div class="section-title">Diagnosticos</div>'
        body += '<div class="data-row">'
        body += _field('Dx Ingreso', f"{hc.get('cod_cie10_ingreso', '')} ")
        body += _field('Dx Egreso', f"{hc.get('cod_cie10_egreso', '')} ")
        body += '</div>'
        dx_rel = _parse_json(hc.get('diagnosticos_relacionados'), [])
        if dx_rel:
            body += f'<div>{_field("Relacionados", ", ".join(str(d) for d in dx_rel))}</div>'
        body += '</div>'

        # Evoluciones
        if evoluciones:
            body += '<div class="section">'
            body += '<div class="section-title">Evoluciones</div>'
            for ev in evoluciones:
                fecha = str(ev.get('creado_en', ''))[:16]
                medico = ev.get('medico_nombre', '')
                body += f'<div style="margin-bottom:12px; padding:8px; border:1px solid #eee; border-radius:4px;">'
                body += f'<div style="font-weight:600; color:#2c3e50; margin-bottom:4px;">{_esc(fecha)} - Dr(a). {_esc(medico)}</div>'
                if ev.get('enfermedad_actual'):
                    body += f'<div><strong>Enfermedad actual:</strong></div><div class="text-block">{_esc(ev["enfermedad_actual"])}</div>'

                sv = _parse_json(ev.get('signos_vitales_json'))
                if sv:
                    body += '<div><strong>Signos vitales:</strong></div><div class="data-row">'
                    for k, v in sv.items():
                        body += _field(k, v)
                    body += '</div>'

                if ev.get('examen_fisico'):
                    body += f'<div><strong>Examen fisico:</strong></div><div class="text-block">{_esc(ev["examen_fisico"])}</div>'
                if ev.get('analisis'):
                    body += f'<div><strong>Analisis:</strong></div><div class="text-block">{_esc(ev["analisis"])}</div>'
                if ev.get('plan_terapeutico'):
                    body += f'<div><strong>Plan terapeutico:</strong></div><div class="text-block">{_esc(ev["plan_terapeutico"])}</div>'
                body += '</div>'
            body += '</div>'

        # Orders and results
        if ordenes:
            body += '<div class="section">'
            body += '<div class="section-title">Ordenes Medicas y Resultados</div>'
            body += '<table><thead><tr><th>Tipo</th><th>Estudio</th><th>CUPS</th><th>Estado</th><th>Resultados</th></tr></thead><tbody>'
            for o in ordenes:
                res_list = resultados_map.get(o['id'], [])
                res_html = ''
                if res_list:
                    for r in res_list:
                        fuera = ' *' if r.get('fuera_rango') else ''
                        res_html += f"{_esc(r.get('parametro',''))}: {_esc(r.get('valor',''))} {_esc(r.get('unidad',''))} (ref: {_esc(r.get('rango_referencia',''))}){fuera}<br>"
                else:
                    res_html = '<em>Pendiente</em>'
                body += f'<tr><td>{_esc(o.get("tipo_orden",""))}</td><td>{_esc(o.get("nombre_estudio",""))}</td>'
                body += f'<td>{_esc(o.get("cod_cups",""))}</td><td>{_esc(o.get("estado",""))}</td>'
                body += f'<td>{res_html}</td></tr>'
            body += '</tbody></table></div>'

        # Prescriptions
        if prescripciones:
            body += '<div class="section">'
            body += '<div class="section-title">Prescripciones</div>'
            body += '<table><thead><tr><th>Medicamento</th><th>Dosis</th><th>Via</th><th>Frecuencia</th><th>Duracion</th><th>Instrucciones</th></tr></thead><tbody>'
            for p in prescripciones:
                body += f'<tr><td>{_esc(p.get("medicamento",""))} {_esc(p.get("concentracion",""))}</td>'
                body += f'<td>{_esc(p.get("dosis",""))}</td><td>{_esc(p.get("via_administracion",""))}</td>'
                body += f'<td>{_esc(p.get("frecuencia",""))}</td><td>{_esc(p.get("duracion",""))}</td>'
                body += f'<td>{_esc(p.get("instrucciones",""))}</td></tr>'
            body += '</tbody></table></div>'

        # Interconsultas
        if interconsultas:
            body += '<div class="section">'
            body += '<div class="section-title">Interconsultas</div>'
            for ic in interconsultas:
                body += f'<div style="margin-bottom:8px; padding:6px; border:1px solid #eee;">'
                body += f'<div>{_field("Especialidad", ic.get("especialidad_solicitada", ""))}'
                body += f'{_field("Estado", ic.get("estado", ""))}'
                body += f'{_field("Solicitante", ic.get("medico_solicitante", ""))}</div>'
                if ic.get('respuesta'):
                    body += f'<div><strong>Respuesta:</strong></div><div class="text-block">{_esc(ic["respuesta"])}</div>'
                body += '</div>'
            body += '</div>'

        # Signature area
        medico_nombre = hc.get('medico_nombre', '')
        body += '<div class="signature-area">'
        body += '<div class="signature-block">'
        body += f'<div class="signature-line">Dr(a). {_esc(medico_nombre)}<br>Medico Tratante</div>'
        body += '</div>'
        body += '<div class="signature-block">'
        body += '<div class="signature-line">Paciente</div>'
        body += '</div>'
        body += '</div>'

        html = _html_doc('Historia Clinica de Urgencias', body, tenant)
        return _html_response(html)
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/impresion/receta/<hc_id> ─────────────────────────────────────────

@impresion_bp.route('/api/impresion/receta/<int:hc_id>')
def impresion_receta(hc_id):
    """Generate prescription document as printable HTML."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        hc = core.row(cur, core.adapt(
            "SELECT * FROM historia_clinica WHERE id=?", db), (hc_id,))
        if not hc:
            return jsonify({'error': 'Historia clínica no encontrada'}), 404

        paciente = core.row(cur, core.adapt(
            "SELECT * FROM pacientes WHERE id=?", db), (hc.get('paciente_id'),))

        prescripciones = core.rows(cur, core.adapt(
            "SELECT * FROM prescripciones WHERE hc_id=? AND estado IN ('prescrita','activa') "
            "ORDER BY creado_en", db), (hc_id,))

        tenant = core.row(cur, core.adapt(
            "SELECT * FROM tenant_config WHERE tenant_id='default'", db)) or {}

        body = ''

        # Patient info
        if paciente:
            nombre = f"{paciente.get('nombres', '')} {paciente.get('apellidos', '')}".strip()
            body += '<div class="section">'
            body += '<div class="section-title">Paciente</div>'
            body += '<div class="data-row">'
            body += _field('Nombre', nombre)
            body += _field('Documento', f"{paciente.get('tipo_doc', 'CC')} {paciente.get('num_doc', '')}")
            body += _field('Fecha Nacimiento', paciente.get('fecha_nacimiento', ''))
            body += _field('EPS', paciente.get('eps', ''))
            body += '</div></div>'

        # Diagnosis
        body += '<div class="section">'
        body += '<div class="section-title">Diagnostico</div>'
        body += f'<div>{_field("CIE-10", hc.get("cod_cie10_ingreso", ""))}</div>'
        body += '</div>'

        # Prescriptions table
        if prescripciones:
            body += '<div class="section">'
            body += '<div class="section-title">Prescripcion Medica</div>'
            body += '<table><thead><tr><th>#</th><th>Medicamento</th><th>Dosis</th>'
            body += '<th>Frecuencia</th><th>Duracion</th><th>Instrucciones</th></tr></thead><tbody>'
            for i, p in enumerate(prescripciones, 1):
                med_name = f"{p.get('medicamento', '')} {p.get('concentracion', '')}".strip()
                if p.get('forma_farmaceutica'):
                    med_name += f" ({p['forma_farmaceutica']})"
                body += f'<tr><td>{i}</td><td>{_esc(med_name)}</td>'
                body += f'<td>{_esc(p.get("dosis", ""))}</td>'
                body += f'<td>{_esc(p.get("frecuencia", ""))}</td>'
                body += f'<td>{_esc(p.get("duracion", ""))}</td>'
                body += f'<td>{_esc(p.get("instrucciones", ""))}</td></tr>'
            body += '</tbody></table></div>'
        else:
            body += '<div class="section"><em>No hay prescripciones activas.</em></div>'

        # Doctor info and signature
        medico_nombre = hc.get('medico_nombre', '')
        body += '<div class="signature-area">'
        body += '<div class="signature-block">'
        body += f'<div class="signature-line">Dr(a). {_esc(medico_nombre)}<br>Medico Tratante</div>'
        body += '</div>'
        body += '</div>'

        html = _html_doc('Receta Medica', body, tenant)
        return _html_response(html)
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/impresion/plan-cuidado/<hc_id> ───────────────────────────────────

@impresion_bp.route('/api/impresion/plan-cuidado/<int:hc_id>')
def impresion_plan_cuidado(hc_id):
    """Generate care plan document as printable HTML."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        hc = core.row(cur, core.adapt(
            "SELECT * FROM historia_clinica WHERE id=?", db), (hc_id,))
        if not hc:
            return jsonify({'error': 'Historia clínica no encontrada'}), 404

        paciente = core.row(cur, core.adapt(
            "SELECT * FROM pacientes WHERE id=?", db), (hc.get('paciente_id'),))

        # Last evolucion for plan and recommendations
        last_evolucion = core.row(cur, core.adapt(
            "SELECT * FROM hc_evoluciones WHERE hc_id=? ORDER BY creado_en DESC LIMIT 1", db),
            (hc_id,))

        prescripciones = core.rows(cur, core.adapt(
            "SELECT * FROM prescripciones WHERE hc_id=? AND estado IN ('prescrita','activa') "
            "ORDER BY creado_en", db), (hc_id,))

        tenant = core.row(cur, core.adapt(
            "SELECT * FROM tenant_config WHERE tenant_id='default'", db)) or {}

        body = ''

        # Patient info
        if paciente:
            nombre = f"{paciente.get('nombres', '')} {paciente.get('apellidos', '')}".strip()
            body += '<div class="section">'
            body += '<div class="section-title">Paciente</div>'
            body += '<div class="data-row">'
            body += _field('Nombre', nombre)
            body += _field('Documento', f"{paciente.get('tipo_doc', 'CC')} {paciente.get('num_doc', '')}")
            body += _field('EPS', paciente.get('eps', ''))
            body += '</div></div>'

        # Diagnosis
        body += '<div class="section">'
        body += '<div class="section-title">Diagnostico</div>'
        body += f'<div>{_field("CIE-10 Ingreso", hc.get("cod_cie10_ingreso", ""))}</div>'
        body += f'<div>{_field("CIE-10 Egreso", hc.get("cod_cie10_egreso", ""))}</div>'
        body += '</div>'

        # Plan terapeutico and recommendations from last evolucion
        if last_evolucion:
            if last_evolucion.get('plan_terapeutico'):
                body += '<div class="section">'
                body += '<div class="section-title">Plan Terapeutico</div>'
                body += f'<div class="text-block">{_esc(last_evolucion["plan_terapeutico"])}</div>'
                body += '</div>'

            # Recomendaciones from campos_custom
            campos = _parse_json(last_evolucion.get('campos_custom'))
            recomendaciones = campos.get('recomendaciones', '')
            if recomendaciones:
                body += '<div class="section">'
                body += '<div class="section-title">Recomendaciones</div>'
                body += f'<div class="text-block">{_esc(recomendaciones)}</div>'
                body += '</div>'

        # Medications
        if prescripciones:
            body += '<div class="section">'
            body += '<div class="section-title">Medicamentos Formulados</div>'
            body += '<table><thead><tr><th>Medicamento</th><th>Dosis</th><th>Via</th>'
            body += '<th>Frecuencia</th><th>Duracion</th><th>Instrucciones</th></tr></thead><tbody>'
            for p in prescripciones:
                med_name = f"{p.get('medicamento', '')} {p.get('concentracion', '')}".strip()
                body += f'<tr><td>{_esc(med_name)}</td><td>{_esc(p.get("dosis", ""))}</td>'
                body += f'<td>{_esc(p.get("via_administracion", ""))}</td>'
                body += f'<td>{_esc(p.get("frecuencia", ""))}</td>'
                body += f'<td>{_esc(p.get("duracion", ""))}</td>'
                body += f'<td>{_esc(p.get("instrucciones", ""))}</td></tr>'
            body += '</tbody></table></div>'

        # Warning signs
        body += '<div class="section">'
        body += '<div class="section-title">Signos de Alarma</div>'
        body += '<div class="text-block">Consulte de inmediato al servicio de urgencias si presenta:\n'
        body += '- Fiebre mayor a 38.5 C que no cede con medicamentos\n'
        body += '- Dolor intenso que no mejora con el tratamiento\n'
        body += '- Dificultad para respirar\n'
        body += '- Sangrado abundante\n'
        body += '- Alteracion del estado de conciencia\n'
        body += '- Vomito persistente o intolerancia a la via oral\n'
        body += '- Cualquier sintoma que considere de gravedad</div>'
        body += '</div>'

        # Follow-up
        body += '<div class="section">'
        body += '<div class="section-title">Control y Seguimiento</div>'
        body += '<div class="text-block">Solicite cita de control con su medico tratante '
        body += 'dentro de los proximos 3 a 7 dias, o antes si presenta signos de alarma.</div>'
        body += '</div>'

        # Signature
        medico_nombre = hc.get('medico_nombre', '')
        body += '<div class="signature-area">'
        body += '<div class="signature-block">'
        body += f'<div class="signature-line">Dr(a). {_esc(medico_nombre)}<br>Medico Tratante</div>'
        body += '</div>'
        body += '<div class="signature-block">'
        body += '<div class="signature-line">Paciente / Acudiente<br>Recibi y comprendo las indicaciones</div>'
        body += '</div>'
        body += '</div>'

        html = _html_doc('Plan de Cuidado al Egreso', body, tenant)
        return _html_response(html)
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/impresion/constancia/<admision_id> ───────────────────────────────

@impresion_bp.route('/api/impresion/constancia/<int:admision_id>')
def impresion_constancia(admision_id):
    """Generate attendance certificate as printable HTML."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        adm = core.row(cur, core.adapt(
            "SELECT * FROM admisiones WHERE id=?", db), (admision_id,))
        if not adm:
            return jsonify({'error': 'Admision no encontrada'}), 404

        paciente = None
        if adm.get('paciente_id'):
            paciente = core.row(cur, core.adapt(
                "SELECT * FROM pacientes WHERE id=?", db), (adm['paciente_id'],))

        hc = core.row(cur, core.adapt(
            "SELECT * FROM historia_clinica WHERE admision_id=?", db), (admision_id,))

        tenant = core.row(cur, core.adapt(
            "SELECT * FROM tenant_config WHERE tenant_id='default'", db)) or {}

        ips_name = tenant.get('nombre_clinica', 'IPS')
        nit = tenant.get('nit', '')

        # Patient name
        if paciente:
            nombre = f"{paciente.get('nombres', '')} {paciente.get('apellidos', '')}".strip()
            num_doc = paciente.get('num_doc', '')
            tipo_doc = paciente.get('tipo_doc', 'CC')
        else:
            nombre = adm.get('nombre_temp', 'No registrado')
            num_doc = adm.get('doc_num_temp', '')
            tipo_doc = adm.get('doc_type_temp', 'CC')

        fecha_ingreso = str(adm.get('creado_en', ''))[:16].replace('T', ' ')
        fecha_egreso = str(adm.get('fecha_salida', ''))[:16].replace('T', ' ') if adm.get('fecha_salida') else 'En atencion'

        dx = ''
        if hc:
            dx = hc.get('cod_cie10_egreso') or hc.get('cod_cie10_ingreso') or ''

        medico = ''
        if hc:
            medico = hc.get('medico_nombre', '')

        body = '<div style="margin-top: 30px; font-size: 12pt; line-height: 2;">'
        body += f'<p>La <strong>{_esc(ips_name)}</strong>, identificada con NIT <strong>{_esc(nit)}</strong>, '
        body += f'certifica que el(la) paciente:</p>'
        body += f'<p style="text-align:center; font-size:14pt; font-weight:700; margin: 20px 0;">'
        body += f'{_esc(nombre)}</p>'
        body += f'<p style="text-align:center;">Identificado(a) con {_esc(tipo_doc)} No. <strong>{_esc(num_doc)}</strong></p>'
        body += f'<p>Fue atendido(a) en el servicio de urgencias de esta institucion:</p>'
        body += '<div style="margin: 15px 30px;">'
        body += f'<div>{_field("Fecha y hora de ingreso", fecha_ingreso)}</div>'
        body += f'<div>{_field("Fecha y hora de egreso", fecha_egreso)}</div>'
        if dx:
            body += f'<div>{_field("Diagnostico", dx)}</div>'
        body += '</div>'
        body += '<p>Se expide la presente constancia a solicitud del interesado(a) para los fines que estime convenientes.</p>'
        body += '</div>'

        # Signature
        body += '<div class="signature-area">'
        body += '<div class="signature-block">'
        if medico:
            body += f'<div class="signature-line">Dr(a). {_esc(medico)}<br>Medico Tratante</div>'
        else:
            body += '<div class="signature-line">Medico Tratante</div>'
        body += '</div>'
        body += '</div>'

        html = _html_doc('Constancia de Atencion', body, tenant)
        return _html_response(html)
    finally:
        cur.close()
        core._return_db(conn, db)
