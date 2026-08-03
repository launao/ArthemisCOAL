"""
roles_engine.py — Blueprint del módulo RBAC de Arthemis Health.

Gestiona usuarios, roles y permisos granulares:
  - CRUD de roles con permisos JSON
  - CRUD de usuarios con asignación de rol
  - Endpoints de dashboard stats por módulo
  - Endpoint de health check de todos los endpoints del sistema
  - Gestión de campos configurables (HC, triage)

Endpoints:
  GET    /api/admin/roles                  — listar roles
  POST   /api/admin/roles                  — crear rol
  PUT    /api/admin/roles/<id>             — editar rol
  DELETE /api/admin/roles/<id>             — eliminar rol (no sistema)
  GET    /api/admin/usuarios               — listar usuarios
  POST   /api/admin/usuarios               — crear usuario
  PUT    /api/admin/usuarios/<id>          — editar usuario
  DELETE /api/admin/usuarios/<id>          — desactivar usuario
  PUT    /api/admin/usuarios/<id>/password — cambiar contraseña
  GET    /api/admin/stats                  — estadísticas del sistema
  GET    /api/admin/health-check           — health check de endpoints
  GET    /api/admin/audit                  — log de auditoría
  GET    /api/admin/campos/<modulo>        — campos configurables
  PUT    /api/admin/campos/<modulo>/<campo>— editar campo
"""

from flask import Blueprint, request, jsonify, session
import json, time, os
from datetime import datetime

roles_bp = Blueprint('roles', __name__)


def _get_deps():
    import core
    return core


def _D(col, db):
    """DATE extraction compatible with both PG and SQLite (Colombia TZ)."""
    return f"CAST({col} AS DATE)" if db == 'pg' else f"DATE({col},'-5 hours')"


def _is_superadmin():
    if not session.get('user_id'):
        return False
    core = _get_deps()
    permisos = core.get_user_permisos()
    return 'superadmin' in permisos


def _has_admin_permiso():
    """Check if user has any admin-level permission."""
    if not session.get('user_id'):
        return False
    core = _get_deps()
    permisos = core.get_user_permisos()
    admin_perms = {'superadmin', 'admin_usuarios', 'admin_roles', 'admin_campos',
                   'admin_sistema', 'director'}
    return bool(admin_perms & set(permisos))


def _has_permiso(required):
    """Check if user has a specific permission or superadmin."""
    if not session.get('user_id'):
        return False
    core = _get_deps()
    permisos = core.get_user_permisos()
    if 'superadmin' in permisos:
        return True
    if isinstance(required, str):
        return required in permisos
    return bool(set(required) & set(permisos))


# ═══════════════════════════════════════════════════════════════════════════════
# ROLES CRUD
# ═══════════════════════════════════════════════════════════════════════════════

@roles_bp.route('/api/admin/roles')
def admin_roles_list():
    """List all roles with user count."""
    if not _has_admin_permiso():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        roles = core.rows(cur, core.adapt(
            "SELECT r.*, "
            "(SELECT COUNT(*) FROM usuarios u WHERE u.rol_id=r.id AND u.activo=1) as usuario_count "
            "FROM roles r ORDER BY r.es_sistema DESC, r.nombre", db))
        # Parse permisos JSON
        for r in roles:
            try:
                r['permisos_list'] = json.loads(r.get('permisos', '[]'))
            except Exception:
                r['permisos_list'] = []
        return jsonify({'roles': roles, 'total': len(roles)})
    finally:
        cur.close()
        core._return_db(conn, db)


@roles_bp.route('/api/admin/roles', methods=['POST'])
def admin_roles_create():
    """Create a new role."""
    if not _has_permiso(['superadmin', 'admin_roles', 'director']):
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    nombre = d.get('nombre', '').strip()
    if not nombre:
        return jsonify({'error': 'Nombre requerido'}), 400

    permisos = d.get('permisos', [])
    if isinstance(permisos, str):
        try:
            permisos = json.loads(permisos)
        except Exception:
            permisos = []

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        existing = core.row(cur, core.adapt("SELECT id FROM roles WHERE nombre=?", db), (nombre,))
        if existing:
            return jsonify({'error': 'Ya existe un rol con ese nombre'}), 409

        cur.execute(core.adapt(
            "INSERT INTO roles(nombre,descripcion,permisos,es_sistema)VALUES(?,?,?,0)", db),
            (nombre, d.get('descripcion', ''), json.dumps(permisos, ensure_ascii=False)))
        conn.commit()

        rol = core.row(cur, core.adapt("SELECT * FROM roles WHERE nombre=?", db), (nombre,))
        core.audit(cur, db, 'roles', rol['id'] if rol else 0, 'crear',
                   f"Rol '{nombre}' creado por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True, 'rol': rol}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


@roles_bp.route('/api/admin/roles/<int:rol_id>', methods=['PUT'])
def admin_roles_update(rol_id):
    """Update an existing role."""
    if not _has_permiso(['superadmin', 'admin_roles', 'director']):
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        rol = core.row(cur, core.adapt("SELECT * FROM roles WHERE id=?", db), (rol_id,))
        if not rol:
            return jsonify({'error': 'Rol no encontrado'}), 404

        nombre = d.get('nombre', rol['nombre']).strip()
        descripcion = d.get('descripcion', rol.get('descripcion', ''))
        permisos = d.get('permisos', None)
        if permisos is not None:
            if isinstance(permisos, str):
                try:
                    permisos = json.loads(permisos)
                except Exception:
                    permisos = []
            permisos_json = json.dumps(permisos, ensure_ascii=False)
        else:
            permisos_json = rol.get('permisos', '[]')

        cur.execute(core.adapt(
            "UPDATE roles SET nombre=?, descripcion=?, permisos=? WHERE id=?", db),
            (nombre, descripcion, permisos_json, rol_id))
        conn.commit()

        core.audit(cur, db, 'roles', rol_id, 'editar',
                   f"Rol '{nombre}' editado por {session.get('usuario', '?')}")
        conn.commit()

        # Update rol_nombre in usuarios table for consistency
        cur.execute(core.adapt(
            "UPDATE usuarios SET rol_nombre=? WHERE rol_id=?", db),
            (nombre, rol_id))
        conn.commit()

        updated = core.row(cur, core.adapt("SELECT * FROM roles WHERE id=?", db), (rol_id,))
        return jsonify({'success': True, 'rol': updated})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


@roles_bp.route('/api/admin/roles/<int:rol_id>', methods=['DELETE'])
def admin_roles_delete(rol_id):
    """Delete a non-system role."""
    if not _is_superadmin():
        return jsonify({'error': 'Solo superadmin puede eliminar roles'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        rol = core.row(cur, core.adapt("SELECT * FROM roles WHERE id=?", db), (rol_id,))
        if not rol:
            return jsonify({'error': 'Rol no encontrado'}), 404
        if rol.get('es_sistema'):
            return jsonify({'error': 'No se puede eliminar un rol de sistema'}), 400

        # Check if any users have this role
        users = core.rows(cur, core.adapt(
            "SELECT id FROM usuarios WHERE rol_id=? AND activo=1", db), (rol_id,))
        if users:
            return jsonify({'error': f'Hay {len(users)} usuario(s) con este rol. Reasigne primero.'}), 400

        cur.execute(core.adapt("DELETE FROM roles WHERE id=?", db), (rol_id,))
        conn.commit()

        core.audit(cur, db, 'roles', rol_id, 'eliminar',
                   f"Rol '{rol['nombre']}' eliminado por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ═══════════════════════════════════════════════════════════════════════════════
# USUARIOS CRUD
# ═══════════════════════════════════════════════════════════════════════════════

@roles_bp.route('/api/admin/usuarios')
def admin_usuarios_list():
    """List all users with role info."""
    if not _has_admin_permiso():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        usuarios = core.rows(cur, core.adapt(
            "SELECT u.id, u.usuario, u.nombre, u.email, u.rol_id, u.rol_nombre, "
            "u.activo, u.ultimo_acceso, u.creado_en, "
            "r.descripcion as rol_descripcion "
            "FROM usuarios u LEFT JOIN roles r ON u.rol_id=r.id "
            "ORDER BY u.activo DESC, u.nombre", db))
        return jsonify({'usuarios': usuarios, 'total': len(usuarios)})
    finally:
        cur.close()
        core._return_db(conn, db)


@roles_bp.route('/api/admin/usuarios', methods=['POST'])
def admin_usuarios_create():
    """Create a new user."""
    if not _has_permiso(['superadmin', 'admin_usuarios', 'director', 'admin_roles']):
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    required = ['usuario', 'nombre', 'password', 'rol_id']
    if not all(d.get(k) for k in required):
        return jsonify({'error': 'Campos requeridos: usuario, nombre, password, rol_id'}), 400

    # Validate password
    ok, msg = core.validate_password(d['password'])
    if not ok:
        return jsonify({'error': msg}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        existing = core.row(cur, core.adapt(
            "SELECT id FROM usuarios WHERE usuario=?", db), (d['usuario'],))
        if existing:
            return jsonify({'error': 'Ya existe un usuario con ese nombre'}), 409

        # Get role name
        rol = core.row(cur, core.adapt("SELECT nombre FROM roles WHERE id=?", db), (d['rol_id'],))
        if not rol:
            return jsonify({'error': 'Rol no encontrado'}), 404

        cur.execute(core.adapt(
            "INSERT INTO usuarios(usuario,nombre,email,pass_hash,rol_id,rol_nombre,activo)"
            "VALUES(?,?,?,?,?,?,1)", db),
            (d['usuario'], d['nombre'], d.get('email', ''),
             core.hash_pass(d['password']), d['rol_id'], rol['nombre']))
        conn.commit()

        user = core.row(cur, core.adapt(
            "SELECT id,usuario,nombre,email,rol_id,rol_nombre,activo,creado_en "
            "FROM usuarios WHERE usuario=?", db), (d['usuario'],))

        core.audit(cur, db, 'usuarios', user['id'] if user else 0, 'crear',
                   f"Usuario '{d['usuario']}' creado con rol '{rol['nombre']}' "
                   f"por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True, 'usuario': user}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


@roles_bp.route('/api/admin/usuarios/<int:user_id>', methods=['PUT'])
def admin_usuarios_update(user_id):
    """Update user info (name, email, role, active status)."""
    if not _has_permiso(['superadmin', 'admin_usuarios', 'director', 'admin_roles']):
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        user = core.row(cur, core.adapt("SELECT * FROM usuarios WHERE id=?", db), (user_id,))
        if not user:
            return jsonify({'error': 'Usuario no encontrado'}), 404

        nombre = d.get('nombre', user['nombre'])
        email = d.get('email', user.get('email', ''))
        activo = d.get('activo', user['activo'])
        rol_id = d.get('rol_id', user['rol_id'])

        # Get role name if changed
        rol_nombre = user['rol_nombre']
        if rol_id != user['rol_id']:
            rol = core.row(cur, core.adapt("SELECT nombre FROM roles WHERE id=?", db), (rol_id,))
            if not rol:
                return jsonify({'error': 'Rol no encontrado'}), 404
            rol_nombre = rol['nombre']

        cur.execute(core.adapt(
            "UPDATE usuarios SET nombre=?, email=?, rol_id=?, rol_nombre=?, activo=? WHERE id=?", db),
            (nombre, email, rol_id, rol_nombre, activo, user_id))
        conn.commit()

        core.audit(cur, db, 'usuarios', user_id, 'editar',
                   f"Usuario '{user['usuario']}' editado por {session.get('usuario', '?')}")
        conn.commit()

        updated = core.row(cur, core.adapt(
            "SELECT id,usuario,nombre,email,rol_id,rol_nombre,activo,ultimo_acceso,creado_en "
            "FROM usuarios WHERE id=?", db), (user_id,))
        return jsonify({'success': True, 'usuario': updated})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


@roles_bp.route('/api/admin/usuarios/<int:user_id>', methods=['DELETE'])
def admin_usuarios_delete(user_id):
    """Deactivate a user (soft delete)."""
    if not _has_permiso(['superadmin', 'admin_usuarios', 'director']):
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        user = core.row(cur, core.adapt("SELECT * FROM usuarios WHERE id=?", db), (user_id,))
        if not user:
            return jsonify({'error': 'Usuario no encontrado'}), 404

        # Can't deactivate yourself
        if user_id == session.get('user_id'):
            return jsonify({'error': 'No puede desactivarse a sí mismo'}), 400

        cur.execute(core.adapt("UPDATE usuarios SET activo=0 WHERE id=?", db), (user_id,))
        conn.commit()

        core.audit(cur, db, 'usuarios', user_id, 'desactivar',
                   f"Usuario '{user['usuario']}' desactivado por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


@roles_bp.route('/api/admin/usuarios/<int:user_id>/password', methods=['PUT'])
def admin_usuarios_password(user_id):
    """Change user password (admin or self)."""
    core = _get_deps()
    # Allow self-change or admin
    if session.get('user_id') != user_id and not _has_permiso(['superadmin', 'admin_usuarios', 'director']):
        return jsonify({'error': 'No autorizado'}), 403

    d = request.json or {}
    new_pw = d.get('password', '')
    ok, msg = core.validate_password(new_pw)
    if not ok:
        return jsonify({'error': msg}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        cur.execute(core.adapt(
            "UPDATE usuarios SET pass_hash=? WHERE id=?", db),
            (core.hash_pass(new_pw), user_id))
        conn.commit()

        core.audit(cur, db, 'usuarios', user_id, 'cambiar_password',
                   f"Contraseña cambiada por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM STATS & HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════════

@roles_bp.route('/api/admin/stats')
def admin_stats():
    """System statistics for dashboard."""
    if not _has_admin_permiso():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        stats = {}

        # Pacientes
        r = core.row(cur, "SELECT COUNT(*) as total FROM pacientes")
        stats['pacientes_total'] = r['total'] if r else 0

        # Admisiones hoy
        T = core.TODAY(db)
        r = core.row(cur, f"SELECT COUNT(*) as total FROM admisiones WHERE {_D('creado_en',db)}={T}")
        stats['admisiones_hoy'] = r['total'] if r else 0

        # Admisiones por estado
        estados = core.rows(cur, "SELECT estado, COUNT(*) as total FROM admisiones GROUP BY estado")
        stats['admisiones_por_estado'] = {e['estado']: e['total'] for e in estados}

        # HC abiertas
        r = core.row(cur, "SELECT COUNT(*) as total FROM historia_clinica WHERE estado='abierta'")
        stats['hc_abiertas'] = r['total'] if r else 0

        # HC total
        r = core.row(cur, "SELECT COUNT(*) as total FROM historia_clinica")
        stats['hc_total'] = r['total'] if r else 0

        # Ordenes pendientes
        r = core.row(cur, "SELECT COUNT(*) as total FROM ordenes_medicas WHERE estado IN ('solicitada','aceptada','en_proceso')")
        stats['ordenes_pendientes'] = r['total'] if r else 0

        # Interconsultas pendientes
        r = core.row(cur, "SELECT COUNT(*) as total FROM interconsultas WHERE estado IN ('solicitada','aceptada')")
        stats['interconsultas_pendientes'] = r['total'] if r else 0

        # Pre-facturas
        r = core.row(cur, "SELECT COUNT(*) as total FROM pre_factura WHERE estado='borrador'")
        stats['prefacturas_pendientes'] = r['total'] if r else 0
        r = core.row(cur, "SELECT COUNT(*) as total FROM pre_factura WHERE estado='aprobada'")
        stats['prefacturas_aprobadas'] = r['total'] if r else 0
        r = core.row(cur, core.adapt(
            "SELECT COALESCE(SUM(total),0) as monto FROM pre_factura WHERE estado='aprobada'", db))
        stats['facturacion_total_aprobada'] = r['monto'] if r else 0

        # Usuarios
        r = core.row(cur, "SELECT COUNT(*) as total FROM usuarios WHERE activo=1")
        stats['usuarios_activos'] = r['total'] if r else 0
        r = core.row(cur, "SELECT COUNT(*) as total FROM roles")
        stats['roles_total'] = r['total'] if r else 0

        # Admisiones últimos 7 días (para gráfica)
        if db == 'pg':
            tendencia = core.rows(cur,
                f"SELECT {_D('creado_en',db)} as fecha, COUNT(*) as total "
                "FROM admisiones WHERE creado_en >= NOW() - INTERVAL '7 days' "
                f"GROUP BY {_D('creado_en',db)} ORDER BY fecha")
        else:
            tendencia = core.rows(cur,
                f"SELECT {_D('creado_en',db)} as fecha, COUNT(*) as total "
                "FROM admisiones WHERE creado_en >= datetime('now','-7 days') "
                f"GROUP BY {_D('creado_en',db)} ORDER BY fecha")
        stats['tendencia_admisiones'] = tendencia

        # Triage por nivel hoy
        triage = core.rows(cur,
            f"SELECT triage_nivel, COUNT(*) as total FROM admisiones "
            f"WHERE triage_nivel IS NOT NULL AND {_D('creado_en',db)}={T} "
            "GROUP BY triage_nivel ORDER BY triage_nivel")
        stats['triage_hoy'] = triage

        # Últimos 5 audit entries
        audits = core.rows(cur,
            "SELECT * FROM audit_trail ORDER BY id DESC LIMIT 5")
        stats['ultimos_audits'] = audits

        stats['timestamp'] = datetime.now().isoformat()
        stats['db_type'] = db

        return jsonify(stats)
    finally:
        cur.close()
        core._return_db(conn, db)


@roles_bp.route('/api/admin/health-check')
def admin_health_check():
    """Check health of all registered endpoints."""
    if not _has_permiso(['superadmin', 'director']):
        return jsonify({'error': 'No autorizado'}), 403

    from flask import current_app
    endpoints = []
    for rule in current_app.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        methods = sorted(rule.methods - {'HEAD', 'OPTIONS'})
        endpoints.append({
            'path': str(rule),
            'methods': methods,
            'endpoint': rule.endpoint,
            'module': rule.endpoint.split('.')[0] if '.' in rule.endpoint else 'app',
        })

    # Group by module
    modules = {}
    for ep in endpoints:
        mod = ep['module']
        if mod not in modules:
            modules[mod] = {'name': mod, 'endpoints': [], 'count': 0}
        modules[mod]['endpoints'].append(ep)
        modules[mod]['count'] += 1

    # DB check
    db_ok = True
    db_info = {}
    try:
        core = _get_deps()
        conn, db = core.get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        db_info = {'status': 'ok', 'type': db}

        # Table counts
        tables = ['pacientes', 'admisiones', 'usuarios', 'roles', 'historia_clinica',
                  'ordenes_medicas', 'interconsultas', 'pre_factura', 'audit_trail']
        table_counts = {}
        for t in tables:
            try:
                r = core.row(cur, f"SELECT COUNT(*) as cnt FROM {t}")
                table_counts[t] = r['cnt'] if r else 0
            except Exception:
                table_counts[t] = -1
        db_info['tables'] = table_counts
        cur.close()
        core._return_db(conn, db)
    except Exception as e:
        db_ok = False
        db_info = {'status': 'error', 'error': str(e)}

    return jsonify({
        'status': 'ok' if db_ok else 'degraded',
        'database': db_info,
        'modules': modules,
        'total_endpoints': len(endpoints),
        'timestamp': datetime.now().isoformat(),
    })


@roles_bp.route('/api/admin/audit')
def admin_audit_log():
    """Get audit trail entries."""
    if not _has_permiso(['superadmin', 'director', 'auditor']):
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))
    entidad = request.args.get('entidad', '').strip()

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        q = "SELECT * FROM audit_trail"
        params = []
        if entidad:
            q += " WHERE entidad=?"
            params.append(entidad)
        q += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        entries = core.rows(cur, core.adapt(q, db), params)
        r = core.row(cur, "SELECT COUNT(*) as total FROM audit_trail")
        total = r['total'] if r else 0

        return jsonify({'entries': entries, 'total': total, 'limit': limit, 'offset': offset})
    finally:
        cur.close()
        core._return_db(conn, db)


# ═══════════════════════════════════════════════════════════════════════════════
# CAMPOS CONFIGURABLES
# ═══════════════════════════════════════════════════════════════════════════════

@roles_bp.route('/api/admin/campos/<modulo>')
def admin_campos_list(modulo):
    """List configurable fields for a module (hc or triage)."""
    if not _has_permiso(['superadmin', 'admin_campos', 'director',
                         'coord_medico', 'coord_enfermeria', 'coord_admisiones']):
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        if modulo == 'hc':
            campos = core.rows(cur, "SELECT * FROM hc_campos_config ORDER BY orden")
        elif modulo == 'triage':
            campos = core.rows(cur, "SELECT * FROM triage_form_config ORDER BY orden")
        else:
            return jsonify({'error': 'Módulo no soportado (use hc o triage)'}), 400

        return jsonify({'campos': campos, 'modulo': modulo, 'total': len(campos)})
    finally:
        cur.close()
        core._return_db(conn, db)


@roles_bp.route('/api/admin/campos/<modulo>/<campo>', methods=['PUT'])
def admin_campos_update(modulo, campo):
    """Update a configurable field."""
    if not _has_permiso(['superadmin', 'admin_campos', 'director',
                         'coord_medico', 'coord_enfermeria', 'coord_admisiones']):
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        if modulo == 'hc':
            table = 'hc_campos_config'
        elif modulo == 'triage':
            table = 'triage_form_config'
        else:
            return jsonify({'error': 'Módulo no soportado'}), 400

        existing = core.row(cur, core.adapt(f"SELECT * FROM {table} WHERE campo=?", db), (campo,))
        if not existing:
            return jsonify({'error': 'Campo no encontrado'}), 404

        # Build update
        updates = []
        params = []
        for key in ['etiqueta', 'tipo', 'requerido', 'visible', 'orden', 'opciones', 'ayuda']:
            if key in d:
                updates.append(f"{key}=?")
                val = d[key]
                if key == 'opciones' and isinstance(val, list):
                    val = json.dumps(val, ensure_ascii=False)
                params.append(val)

        if not updates:
            return jsonify({'error': 'Nada que actualizar'}), 400

        updates.append("modificado_por=?")
        params.append(session.get('usuario', 'sistema'))

        params.append(campo)
        cur.execute(core.adapt(
            f"UPDATE {table} SET {','.join(updates)} WHERE campo=?", db), params)
        conn.commit()

        core.audit(cur, db, 'campos', campo, 'editar',
                   f"Campo '{campo}' de {modulo} editado por {session.get('usuario', '?')}")
        conn.commit()

        updated = core.row(cur, core.adapt(f"SELECT * FROM {table} WHERE campo=?", db), (campo,))
        return jsonify({'success': True, 'campo': updated})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD DATA ENDPOINTS (role-specific)
# ═══════════════════════════════════════════════════════════════════════════════

@roles_bp.route('/api/dashboard/medico')
def dashboard_medico():
    """Dashboard data for doctors."""
    if not _has_permiso(['superadmin', 'historia_clinica', 'director']):
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        data = {}
        # HC abiertas asignadas
        data['hc_abiertas'] = core.rows(cur, core.adapt(
            "SELECT hc.*, p.nombres, p.apellidos, p.num_doc, a.turno, a.triage_nivel "
            "FROM historia_clinica hc "
            "LEFT JOIN pacientes p ON hc.paciente_id=p.id "
            "LEFT JOIN admisiones a ON hc.admision_id=a.id "
            "WHERE hc.estado='abierta' ORDER BY hc.creado_en DESC", db))

        # Ordenes pendientes
        r = core.row(cur, "SELECT COUNT(*) as total FROM ordenes_medicas WHERE estado='solicitada'")
        data['ordenes_pendientes'] = r['total'] if r else 0

        # Interconsultas pendientes
        r = core.row(cur, "SELECT COUNT(*) as total FROM interconsultas WHERE estado IN ('solicitada','aceptada')")
        data['interconsultas_pendientes'] = r['total'] if r else 0

        # Pacientes atendidos hoy
        T = core.TODAY(db)
        r = core.row(cur, f"SELECT COUNT(*) as total FROM historia_clinica WHERE {_D('creado_en',db)}={T}")
        data['atendidos_hoy'] = r['total'] if r else 0

        return jsonify(data)
    finally:
        cur.close()
        core._return_db(conn, db)


@roles_bp.route('/api/dashboard/enfermeria')
def dashboard_enfermeria():
    """Dashboard data for nursing staff."""
    if not _has_permiso(['superadmin', 'enfermeria', 'historia_clinica', 'director']):
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        data = {}

        # Pendientes de triage
        r = core.row(cur, "SELECT COUNT(*) as total FROM admisiones WHERE estado='kiosco'")
        data['pendientes_triage'] = r['total'] if r else 0

        # En triage ahora
        r = core.row(cur, "SELECT COUNT(*) as total FROM admisiones WHERE estado='triaje'")
        data['en_triage'] = r['total'] if r else 0

        # Triage completados hoy
        T2 = core.TODAY(db)
        triage_hoy = core.rows(cur,
            f"SELECT triage_nivel, COUNT(*) as total FROM admisiones "
            f"WHERE triage_nivel IS NOT NULL AND {_D('creado_en',db)}={T2} "
            "GROUP BY triage_nivel")
        data['triage_hoy_por_nivel'] = triage_hoy

        # Medicamentos pendientes (prescripciones)
        r = core.row(cur, "SELECT COUNT(*) as total FROM prescripciones WHERE estado='prescrita'")
        data['medicamentos_pendientes'] = r['total'] if r else 0

        return jsonify(data)
    finally:
        cur.close()
        core._return_db(conn, db)


@roles_bp.route('/api/dashboard/admisiones')
def dashboard_admisiones_stats():
    """Dashboard data for admissions."""
    if not _has_permiso(['superadmin', 'admisiones', 'director']):
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        data = {}

        # Por estado
        estados = core.rows(cur, "SELECT estado, COUNT(*) as total FROM admisiones GROUP BY estado")
        data['por_estado'] = {e['estado']: e['total'] for e in estados}

        # En cola
        r = core.row(cur, "SELECT COUNT(*) as total FROM admisiones WHERE estado IN ('kiosco','llamando')")
        data['en_cola'] = r['total'] if r else 0

        # Promedio espera (approx)
        data['promedio_espera_min'] = 0
        try:
            r = core.row(cur, "SELECT AVG(tiempo_espera_min) as avg FROM admisiones WHERE tiempo_espera_min > 0")
            data['promedio_espera_min'] = round(r['avg'], 1) if r and r['avg'] else 0
        except Exception:
            pass

        # Hoy total
        T3 = core.TODAY(db)
        r = core.row(cur, f"SELECT COUNT(*) as total FROM admisiones WHERE {_D('creado_en',db)}={T3}")
        data['total_hoy'] = r['total'] if r else 0

        return jsonify(data)
    finally:
        cur.close()
        core._return_db(conn, db)


@roles_bp.route('/api/dashboard/financiero')
def dashboard_financiero():
    """Dashboard data for finance."""
    if not _has_permiso(['superadmin', 'facturacion', 'director', 'coord_financiero']):
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        data = {}

        # Pre-facturas por estado
        pf_estados = core.rows(cur,
            "SELECT estado, COUNT(*) as total, COALESCE(SUM(total),0) as monto "
            "FROM pre_factura GROUP BY estado")
        data['prefacturas'] = {e['estado']: {'count': e['total'], 'monto': e['monto']} for e in pf_estados}

        # Total facturado (aprobadas)
        r = core.row(cur, "SELECT COALESCE(SUM(total),0) as monto FROM pre_factura WHERE estado='aprobada'")
        data['total_facturado'] = r['monto'] if r else 0

        # Copagos cobrados
        r = core.row(cur, "SELECT COALESCE(SUM(copago),0) as monto FROM pre_factura WHERE estado='aprobada'")
        data['total_copagos'] = r['monto'] if r else 0

        # Pendientes aprobación
        r = core.row(cur, "SELECT COUNT(*) as total FROM pre_factura WHERE estado='borrador'")
        data['pendientes_aprobacion'] = r['total'] if r else 0

        return jsonify(data)
    finally:
        cur.close()
        core._return_db(conn, db)


# ═══════════════════════════════════════════════════════════════════════════════
# PERMISSIONS CATALOG (for UI)
# ═══════════════════════════════════════════════════════════════════════════════

@roles_bp.route('/api/admin/permisos-catalogo')
def permisos_catalogo():
    """Return the full catalog of available permissions for role configuration."""
    if not _has_admin_permiso():
        return jsonify({'error': 'No autorizado'}), 403

    catalogo = [
        # System
        {'key': 'superadmin', 'label': 'Superadmin', 'group': 'Sistema', 'desc': 'Acceso total al sistema'},
        {'key': 'director', 'label': 'Director/Gerente', 'group': 'Sistema', 'desc': 'Visión ejecutiva completa'},
        {'key': 'admin_usuarios', 'label': 'Admin Usuarios', 'group': 'Administración', 'desc': 'Crear, editar, desactivar usuarios'},
        {'key': 'admin_roles', 'label': 'Admin Roles', 'group': 'Administración', 'desc': 'Crear y editar roles'},
        {'key': 'admin_campos', 'label': 'Admin Campos', 'group': 'Administración', 'desc': 'Configurar campos de formularios'},
        {'key': 'admin_sistema', 'label': 'Admin Sistema', 'group': 'Administración', 'desc': 'Configuración del sistema'},
        # Clinical
        {'key': 'kiosco', 'label': 'Kiosco', 'group': 'Módulos', 'desc': 'Registro de pacientes en kiosco'},
        {'key': 'admisiones', 'label': 'Admisiones', 'group': 'Módulos', 'desc': 'Gestión de admisiones'},
        {'key': 'historia_clinica', 'label': 'Historia Clínica', 'group': 'Módulos', 'desc': 'Lectura/escritura de HC'},
        {'key': 'historia_clinica_read', 'label': 'HC Solo Lectura', 'group': 'Módulos', 'desc': 'Solo lectura de historias clínicas'},
        {'key': 'enfermeria', 'label': 'Enfermería', 'group': 'Módulos', 'desc': 'Triage, signos vitales, medicamentos'},
        {'key': 'ordenes', 'label': 'Órdenes Médicas', 'group': 'Módulos', 'desc': 'Crear y gestionar órdenes'},
        {'key': 'interconsultas', 'label': 'Interconsultas', 'group': 'Módulos', 'desc': 'Solicitar y responder interconsultas'},
        {'key': 'prescripciones', 'label': 'Prescripciones', 'group': 'Módulos', 'desc': 'Prescribir medicamentos'},
        {'key': 'laboratorio', 'label': 'Laboratorio', 'group': 'Módulos', 'desc': 'Cola de órdenes lab, resultados'},
        {'key': 'imagenes', 'label': 'Imágenes', 'group': 'Módulos', 'desc': 'Cola de órdenes img, resultados'},
        {'key': 'farmacia', 'label': 'Farmacia', 'group': 'Módulos', 'desc': 'Despacho de medicamentos'},
        # Finance
        {'key': 'facturacion', 'label': 'Facturación', 'group': 'Finanzas', 'desc': 'Pre-facturas, RIPS'},
        {'key': 'facturacion_aprobar', 'label': 'Aprobar Facturas', 'group': 'Finanzas', 'desc': 'Aprobación de pre-facturas'},
        {'key': 'cobros', 'label': 'Cobros/Caja', 'group': 'Finanzas', 'desc': 'Cobro de copagos'},
        # Coordination
        {'key': 'coord_medico', 'label': 'Coord. Médico', 'group': 'Coordinación', 'desc': 'Campos HC, horarios médicos'},
        {'key': 'coord_enfermeria', 'label': 'Coord. Enfermería', 'group': 'Coordinación', 'desc': 'Campos triage, horarios enfermería'},
        {'key': 'coord_admisiones', 'label': 'Coord. Admisiones', 'group': 'Coordinación', 'desc': 'Supervisión admisiones'},
        {'key': 'coord_financiero', 'label': 'Coord. Financiero', 'group': 'Coordinación', 'desc': 'Supervisión financiera'},
        # Other
        {'key': 'reportes', 'label': 'Reportes', 'group': 'Otros', 'desc': 'Generar reportes'},
        {'key': 'auditor', 'label': 'Auditoría', 'group': 'Otros', 'desc': 'Acceso de solo lectura total, calidad'},
        {'key': 'agendamiento', 'label': 'Agendamiento', 'group': 'Otros', 'desc': 'Gestión de citas'},
        {'key': 'inventario', 'label': 'Inventario', 'group': 'Otros', 'desc': 'Control de inventario'},
    ]

    return jsonify({'catalogo': catalogo})
