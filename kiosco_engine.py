"""
kiosco_engine.py — Blueprint del módulo Kiosco de Arthemis Health.

Endpoint público (sin auth) para auto-registro de pacientes en el kiosco:
  POST /api/kiosco/anuncio — genera turno y admisión en estado 'kiosco'

Flujo:
  1. Paciente ingresa cédula en kiosco (frontend)
  2. Frontend busca paciente via GET /api/pacientes/<num_doc> (en app.py)
  3. Paciente elige prioridad, autoriza habeas data, elige notificación
  4. Frontend llama POST /api/kiosco/anuncio
  5. Backend genera turno (PP=preferencial, PC=cita, PS=sin cita)
  6. Crea admisión en estado='kiosco', notifica recepción via SSE
"""

from datetime import datetime
from flask import Blueprint, request, jsonify

kiosco_bp = Blueprint('kiosco', __name__)

def _get_deps():
    """Lazy import to avoid circular dependencies."""
    import core
    return core

# ── POST /api/kiosco/anuncio ─────────────────────────────────────────────────

@kiosco_bp.route('/api/kiosco/anuncio', methods=['POST'])
def kiosco_anuncio():
    """Registro público desde kiosco. Genera turno + admisión."""
    core = _get_deps()
    d = request.json
    if not d:
        return jsonify({'error': 'Datos requeridos'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()

    try:
        T = core.TODAY(db)

        # Determine alert color
        color = 'yellow'
        if d.get('turno_tipo') == 'preferencial':
            color = 'red'
        elif d.get('tipo_atencion') == 'cita_programada':
            color = 'green'

        # Generate admission ID
        cur.execute(f"SELECT COUNT(*) FROM admisiones WHERE DATE(creado_en)={T}")
        n = cur.fetchone()[0]
        id_adm = f"ADM{str(n + 1).zfill(3)}-{datetime.now().strftime('%Y%m%d')}"

        # Generate turno: PP (preferencial), PC (cita programada), PS (sin cita)
        pref = 'PP' if d.get('turno_tipo') == 'preferencial' else (
            'PC' if d.get('tipo_atencion') == 'cita_programada' else 'PS')
        cur.execute(
            core.adapt(f"SELECT COUNT(*) FROM admisiones WHERE turno LIKE ? AND DATE(creado_en)={T}", db),
            (f'{pref}%',))
        nt = cur.fetchone()[0]
        turno = f"{pref}{nt + 1}"

        # Set habeas data fields
        habeas = 1 if d.get('habeas_data') else 0
        habeas_ts = datetime.now().isoformat() if habeas else None

        # Insert admission
        cur.execute(core.adapt(
            "INSERT INTO admisiones(id_adm,paciente_id,estado,tipo_atencion,turno,turno_tipo,"
            "tiempo_espera_min,color_alerta,origen,doc_num_temp,doc_type_temp,nombre_temp,"
            "celular_notif,habeas_data,habeas_data_ts)VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", db),
            (id_adm, d.get('paciente_id'), 'kiosco',
             d.get('tipo_atencion', 'sin_cita'), turno,
             d.get('turno_tipo', 'general'),
             5 if color == 'red' else 15, color, 'kiosco',
             d.get('doc_num'), d.get('doc_type', 'CC'),
             d.get('nombre'), d.get('celular'),
             habeas, habeas_ts))
        conn.commit()

        # Get inserted ID
        a = core.row(cur, core.adapt("SELECT id FROM admisiones WHERE id_adm=?", db), (id_adm,))

        # Audit
        core.audit(cur, db, 'admisiones', a['id'], 'kiosco_anuncio',
                    f"Turno {turno} asignado desde kiosco")

        # Notification for reception
        cur.execute(core.adapt(
            "INSERT INTO notificaciones(tipo,destinatario,mensaje,admision_id)VALUES(?,?,?,?)", db),
            ('nuevo_turno', 'recepcion',
             f"Nuevo turno {turno} — {d.get('doc_num', '')}", a['id']))
        conn.commit()

        # SSE broadcast
        core.sse_broadcast({
            'tipo': 'nuevo_turno',
            'turno': turno,
            'turno_tipo': d.get('turno_tipo', 'general'),
            'color_alerta': color,
            'id': a['id'],
            'id_adm': id_adm,
            'nombre': d.get('nombre') or d.get('doc_num', ''),
        })

        return jsonify({
            'success': True,
            'turno': turno,
            'id_adm': id_adm,
            'id': a['id'],
            'color_alerta': color,
            'turno_tipo': d.get('turno_tipo', 'general'),
        })

    except Exception as e:
        conn.rollback()
        return jsonify({'error': f'Error interno: {str(e)}'}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/kiosco/turnos-hoy ───────────────────────────────────────────────

@kiosco_bp.route('/api/kiosco/turnos-hoy')
def kiosco_turnos_hoy():
    """Returns today's turn counts per type (for display/stats)."""
    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    T = core.TODAY(db)

    total = core.rows(cur, f"SELECT turno_tipo, COUNT(*) as total FROM admisiones WHERE DATE(creado_en)={T} GROUP BY turno_tipo")
    en_espera = core.rows(cur, core.adapt(
        f"SELECT turno_tipo, COUNT(*) as total FROM admisiones WHERE DATE(creado_en)={T} AND estado=? GROUP BY turno_tipo", db),
        ('kiosco',))

    cur.close()
    core._return_db(conn, db)

    return jsonify({
        'total': {r['turno_tipo']: r['total'] for r in total},
        'en_espera': {r['turno_tipo']: r['total'] for r in en_espera},
    })
