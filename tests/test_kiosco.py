"""
tests/test_kiosco.py — Tests completos del módulo Kiosco de ArthemisCOAL.

Cubre:
  1. Endpoint POST /api/kiosco/anuncio (público, sin auth)
  2. Generación de turnos (PP, PC, PS)
  3. Colores de alerta según prioridad
  4. Habeas data
  5. Paciente registrado vs nuevo (doc_num_temp)
  6. Múltiples turnos secuenciales
  7. GET /api/kiosco/turnos-hoy
  8. Lookup de paciente GET /api/pacientes/<num_doc>
  9. Creación de paciente POST /api/pacientes
  10. Endpoints de soporte: /health, /api/config, /api/auth/*
  11. SSE endpoint
  12. Seguridad: CSRF, headers
"""

import json
import pytest
from conftest import create_test_patient


# ── HEALTH & CONFIG ──────────────────────────────────────────────────────────

class TestHealthAndConfig:
    def test_health_endpoint(self, client):
        resp = client.get('/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'ok'
        assert data['db'] == 'sqlite'
        assert 'ts' in data

    def test_config_endpoint(self, client):
        resp = client.get('/api/config')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'modulos' in data
        assert 'kiosco' in data['modulos']

    def test_index_page(self, client):
        resp = client.get('/')
        assert resp.status_code == 200

    def test_kiosco_page(self, client):
        resp = client.get('/kiosco')
        assert resp.status_code == 200
        assert b'Arthemis Health' in resp.data


# ── AUTH ──────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_login_success(self, client, admin_password, _setup_admin):
        resp = client.post('/api/auth/login', json={
            'usuario': 'admin',
            'password': admin_password,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['rol'] == 'Superadmin'

    def test_login_wrong_password(self, client, _setup_admin):
        resp = client.post('/api/auth/login', json={
            'usuario': 'admin',
            'password': 'wrongpassword',
        })
        assert resp.status_code == 401

    def test_login_missing_fields(self, client):
        resp = client.post('/api/auth/login', json={'usuario': ''})
        assert resp.status_code == 400

    def test_auth_me_unauthenticated(self, client):
        resp = client.get('/api/auth/me')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['autenticado'] is False

    def test_auth_me_authenticated(self, auth_client):
        resp = auth_client.get('/api/auth/me')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['autenticado'] is True
        assert data['usuario'] == 'admin'

    def test_logout(self, auth_client):
        resp = auth_client.post('/api/auth/logout')
        assert resp.status_code == 200
        # After logout, /api/auth/me should say unauthenticated
        resp2 = auth_client.get('/api/auth/me')
        data = resp2.get_json()
        assert data['autenticado'] is False


# ── PACIENTES ─────────────────────────────────────────────────────────────────

class TestPacientes:
    def test_lookup_existing_patient(self, client):
        """Seed patients should be findable."""
        resp = client.get('/api/pacientes/1023456789')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['nombres'] == 'Juan Carlos'
        assert data['apellidos'] == 'Salcedo Gómez'
        assert 'citas_futuras' in data

    def test_lookup_nonexistent_patient(self, client):
        resp = client.get('/api/pacientes/9999999999')
        assert resp.status_code == 404

    def test_create_patient_requires_auth(self, client):
        resp = client.post('/api/pacientes', json={
            'num_doc': '1111111111',
            'nombres': 'Test',
            'apellidos': 'User',
        })
        assert resp.status_code == 401

    def test_create_patient(self, auth_client):
        data, num_doc = create_test_patient(auth_client, num_doc='8888888888')
        assert 'id' in data
        assert data['nombres'] == 'Test'

    def test_create_duplicate_patient(self, auth_client):
        create_test_patient(auth_client, num_doc='7777777777')
        resp = auth_client.post('/api/pacientes', json={
            'num_doc': '7777777777',
            'nombres': 'Otro',
            'apellidos': 'Duplicado',
        })
        assert resp.status_code == 409


# ── KIOSCO ANUNCIO ────────────────────────────────────────────────────────────

class TestKioscoAnuncio:
    def test_anuncio_sin_cita_turno_general(self, client):
        """Paciente sin cita, turno general => PS prefix, color yellow."""
        resp = client.post('/api/kiosco/anuncio', json={
            'paciente_id': None,
            'doc_num': '9999000001',
            'doc_type': 'CC',
            'nombre': 'Test Kiosco',
            'tipo_atencion': 'sin_cita',
            'turno_tipo': 'general',
            'habeas_data': True,
            'celular': '3001112233',
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['turno'].startswith('PS')
        assert data['color_alerta'] == 'yellow'
        assert data['turno_tipo'] == 'general'
        assert 'id_adm' in data
        assert 'id' in data

    def test_anuncio_preferencial(self, client):
        """Turno preferencial => PP prefix, color red."""
        resp = client.post('/api/kiosco/anuncio', json={
            'paciente_id': None,
            'doc_num': '9999000002',
            'doc_type': 'CC',
            'nombre': 'Embarazada Test',
            'tipo_atencion': 'sin_cita',
            'turno_tipo': 'preferencial',
            'habeas_data': True,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['turno'].startswith('PP')
        assert data['color_alerta'] == 'red'
        assert data['turno_tipo'] == 'preferencial'

    def test_anuncio_cita_programada(self, client):
        """Cita programada => PC prefix, color green."""
        resp = client.post('/api/kiosco/anuncio', json={
            'paciente_id': 1,
            'doc_num': '1023456789',
            'doc_type': 'CC',
            'tipo_atencion': 'cita_programada',
            'turno_tipo': 'general',
            'habeas_data': True,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['turno'].startswith('PC')
        assert data['color_alerta'] == 'green'

    def test_anuncio_with_registered_patient(self, auth_client, client):
        """Paciente registrado — paciente_id should be set."""
        pac, num_doc = create_test_patient(auth_client, num_doc='5555555555')
        resp = client.post('/api/kiosco/anuncio', json={
            'paciente_id': pac['id'],
            'doc_num': num_doc,
            'doc_type': 'CC',
            'tipo_atencion': 'sin_cita',
            'turno_tipo': 'general',
            'habeas_data': False,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True

    def test_anuncio_new_patient_nombre_temp(self, client):
        """Paciente nuevo: nombre_temp should be stored."""
        resp = client.post('/api/kiosco/anuncio', json={
            'paciente_id': None,
            'doc_num': '9999000099',
            'doc_type': 'TI',
            'nombre': 'María Nueva',
            'tipo_atencion': 'sin_cita',
            'turno_tipo': 'general',
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True

        # Verify admission was stored with nombre_temp
        import core
        conn, db = core.get_db()
        cur = conn.cursor()
        adm = core.row(cur, "SELECT * FROM admisiones WHERE id_adm=?", (data['id_adm'],))
        assert adm['nombre_temp'] == 'María Nueva'
        assert adm['doc_num_temp'] == '9999000099'
        assert adm['doc_type_temp'] == 'TI'
        cur.close()
        conn.close()

    def test_anuncio_habeas_data_stored(self, client):
        """Habeas data flag and timestamp should be stored."""
        resp = client.post('/api/kiosco/anuncio', json={
            'paciente_id': None,
            'doc_num': '9999000088',
            'doc_type': 'CC',
            'nombre': 'Habeas Test',
            'tipo_atencion': 'sin_cita',
            'turno_tipo': 'general',
            'habeas_data': True,
        })
        data = resp.get_json()
        assert data['success'] is True

        import core
        conn, db = core.get_db()
        cur = conn.cursor()
        adm = core.row(cur, "SELECT * FROM admisiones WHERE id_adm=?", (data['id_adm'],))
        assert adm['habeas_data'] == 1
        assert adm['habeas_data_ts'] is not None
        cur.close()
        conn.close()

    def test_anuncio_sequential_turnos(self, client):
        """Multiple turnos of the same type should be sequential."""
        turnos = []
        for i in range(3):
            resp = client.post('/api/kiosco/anuncio', json={
                'paciente_id': None,
                'doc_num': f'6666000{i:03d}',
                'doc_type': 'CC',
                'nombre': f'Seq Test {i}',
                'tipo_atencion': 'sin_cita',
                'turno_tipo': 'preferencial',
                'habeas_data': False,
            })
            data = resp.get_json()
            assert data['success'] is True
            turnos.append(data['turno'])

        # All should start with PP and have increasing numbers
        for t in turnos:
            assert t.startswith('PP')
        nums = [int(t[2:]) for t in turnos]
        assert nums == sorted(nums)
        # Each should be unique
        assert len(set(turnos)) == len(turnos)

    def test_anuncio_creates_notification(self, client):
        """Anuncio should create a notification for recepción."""
        resp = client.post('/api/kiosco/anuncio', json={
            'paciente_id': None,
            'doc_num': '9999000077',
            'doc_type': 'CC',
            'nombre': 'Notif Test',
            'tipo_atencion': 'sin_cita',
            'turno_tipo': 'general',
        })
        data = resp.get_json()
        assert data['success'] is True

        import core
        conn, db = core.get_db()
        cur = conn.cursor()
        notif = core.row(cur, "SELECT * FROM notificaciones WHERE admision_id=?", (data['id'],))
        assert notif is not None
        assert notif['tipo'] == 'nuevo_turno'
        assert notif['destinatario'] == 'recepcion'
        assert data['turno'] in notif['mensaje']
        cur.close()
        conn.close()

    def test_anuncio_creates_audit(self, client):
        """Anuncio should create an audit trail entry."""
        resp = client.post('/api/kiosco/anuncio', json={
            'paciente_id': None,
            'doc_num': '9999000066',
            'doc_type': 'CC',
            'nombre': 'Audit Test',
            'tipo_atencion': 'sin_cita',
            'turno_tipo': 'general',
        })
        data = resp.get_json()

        import core
        conn, db = core.get_db()
        cur = conn.cursor()
        trail = core.row(cur, "SELECT * FROM audit_trail WHERE entidad='admisiones' AND entidad_id=?",
                         (str(data['id']),))
        assert trail is not None
        assert trail['accion'] == 'kiosco_anuncio'
        assert data['turno'] in trail['detalle']
        cur.close()
        conn.close()

    def test_anuncio_empty_body_rejected(self, client):
        """POST with no JSON body should return 400."""
        resp = client.post('/api/kiosco/anuncio',
                           data='',
                           content_type='application/json')
        # Flask may return 400 or our handler returns 400
        assert resp.status_code in (400, 415)

    def test_anuncio_no_auth_required(self, client):
        """Kiosco anuncio is a public endpoint — no auth needed."""
        resp = client.post('/api/kiosco/anuncio', json={
            'doc_num': '9999000055',
            'doc_type': 'CC',
            'nombre': 'Public Test',
            'tipo_atencion': 'sin_cita',
            'turno_tipo': 'general',
        })
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True


# ── TURNOS HOY ────────────────────────────────────────────────────────────────

class TestTurnosHoy:
    def test_turnos_hoy_endpoint(self, client):
        """GET /api/kiosco/turnos-hoy should return turn counts."""
        # Create a turn first
        client.post('/api/kiosco/anuncio', json={
            'doc_num': '9999111111',
            'doc_type': 'CC',
            'nombre': 'Stats Test',
            'tipo_atencion': 'sin_cita',
            'turno_tipo': 'general',
        })

        resp = client.get('/api/kiosco/turnos-hoy')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'total' in data
        assert 'en_espera' in data


# ── SECURITY ──────────────────────────────────────────────────────────────────

class TestSecurity:
    def test_security_headers(self, client):
        """Security headers should be present."""
        resp = client.get('/health')
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
        assert resp.headers.get('X-Frame-Options') == 'SAMEORIGIN'
        assert resp.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'

    def test_csrf_blocks_foreign_origin(self, client):
        """POST from unknown origin should be blocked."""
        resp = client.post('/api/auth/login',
                           json={'usuario': 'x', 'password': 'y'},
                           headers={'Origin': 'https://evil.example.com'})
        assert resp.status_code == 403
        assert 'Origen no permitido' in resp.get_json()['error']

    def test_csrf_allows_known_origin(self, client, admin_password, _setup_admin):
        """POST from allowed origin should work."""
        resp = client.post('/api/auth/login',
                           json={'usuario': 'admin', 'password': admin_password},
                           headers={'Origin': 'http://localhost:5050'})
        assert resp.status_code == 200

    def test_csrf_exempt_kiosco(self, client):
        """Kiosco anuncio is CSRF exempt — any origin should work."""
        resp = client.post('/api/kiosco/anuncio',
                           json={
                               'doc_num': '9999222222',
                               'doc_type': 'CC',
                               'nombre': 'CSRF Test',
                               'tipo_atencion': 'sin_cita',
                               'turno_tipo': 'general',
                           },
                           headers={'Origin': 'https://evil.example.com'})
        assert resp.status_code == 200


# ── SSE ───────────────────────────────────────────────────────────────────────

class TestSSE:
    def test_sse_endpoint_exists(self, client):
        """SSE endpoint should respond with event-stream content type."""
        resp = client.get('/api/sse')
        assert resp.content_type.startswith('text/event-stream')


# ── CORE HELPERS ──────────────────────────────────────────────────────────────

class TestCoreHelpers:
    def test_adapt_sqlite(self):
        import core
        q = "SELECT * FROM t WHERE x=? AND y LIKE ?"
        assert core.adapt(q, 'sqlite') == q

    def test_adapt_pg(self):
        import core
        q = "SELECT * FROM t WHERE x=? AND y LIKE ?"
        adapted = core.adapt(q, 'pg')
        assert '%s' in adapted
        assert 'ILIKE' in adapted

    def test_now_today(self):
        import core
        assert core.NOW('pg') == 'NOW()'
        assert core.NOW('sqlite') == "datetime('now')"
        assert core.TODAY('pg') == 'CURRENT_DATE'
        assert core.TODAY('sqlite') == "DATE('now')"

    def test_jstr(self):
        import core
        assert core.jstr([1, 2]) == '[1, 2]'
        assert core.jstr({'a': 1}) == '{"a": 1}'
        assert core.jstr(None) == '[]'
        assert core.jstr('hello') == 'hello'

    def test_validate_password(self):
        import core
        ok, _ = core.validate_password('Ab123456')
        assert ok is True
        ok, msg = core.validate_password('short')
        assert ok is False
        ok, msg = core.validate_password('alllowercase1')
        assert ok is False
        ok, msg = core.validate_password('ALLUPPERCASE')
        assert ok is False

    def test_hash_verify(self):
        import core
        h = core.hash_pass('TestPass123')
        ok, upgrade = core.verify_pass('TestPass123', h)
        assert ok is True
        assert upgrade is False
        ok, _ = core.verify_pass('WrongPass', h)
        assert ok is False

    def test_rows_row_empty(self):
        import core
        conn, db = core.get_db()
        cur = conn.cursor()
        result = core.rows(cur, "SELECT * FROM pacientes WHERE num_doc='nonexistent_999'")
        assert result == []
        result = core.row(cur, "SELECT * FROM pacientes WHERE num_doc='nonexistent_999'")
        assert result is None
        cur.close()
        conn.close()


# ── NEW ENDPOINTS (v2) ──────────────────────────────────────────────────────

class TestKioscoCola:
    """Tests for GET /api/kiosco/cola."""

    def test_cola_returns_empty(self, client):
        resp = client.get('/api/kiosco/cola')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'cola' in data
        assert isinstance(data['cola'], list)

    def test_cola_after_anuncio(self, client):
        """After an anuncio, the turn should appear in the queue."""
        client.post('/api/kiosco/anuncio', json={
            'doc_num': '7770001111',
            'nombre': 'Cola Test',
            'tipo_atencion': 'sin_cita',
            'turno_tipo': 'general',
            'servicio_nombre': 'Optometría',
        })
        resp = client.get('/api/kiosco/cola')
        data = resp.get_json()
        turnos = [t for t in data['cola'] if t['nombre'] == 'Cola Test']
        assert len(turnos) >= 1
        assert turnos[0]['estado'] == 'kiosco'
        assert turnos[0]['servicio'] == 'Optometría'


class TestKioscoLlamarTurno:
    """Tests for POST /api/kiosco/llamar-turno."""

    def test_llamar_requires_id(self, client):
        resp = client.post('/api/kiosco/llamar-turno', json={})
        assert resp.status_code == 400

    def test_llamar_turno(self, client):
        """Call a turn and verify state change."""
        resp = client.post('/api/kiosco/anuncio', json={
            'doc_num': '7770002222',
            'nombre': 'Llamar Test',
            'tipo_atencion': 'sin_cita',
            'turno_tipo': 'general',
        })
        turno_data = resp.get_json()
        turno_id = turno_data['id']

        resp = client.post('/api/kiosco/llamar-turno', json={'id': turno_id})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['nombre'] == 'Llamar Test'

        resp = client.get('/api/kiosco/cola')
        cola = resp.get_json()['cola']
        found = [t for t in cola if t['id'] == turno_id]
        assert found[0]['estado'] == 'llamando'


class TestKioscoAtenderTurno:
    """Tests for POST /api/kiosco/atender-turno."""

    def test_atender_turno(self, client):
        resp = client.post('/api/kiosco/anuncio', json={
            'doc_num': '7770003333',
            'nombre': 'Atender Test',
            'tipo_atencion': 'sin_cita',
            'turno_tipo': 'general',
        })
        turno_id = resp.get_json()['id']
        client.post('/api/kiosco/llamar-turno', json={'id': turno_id})

        resp = client.post('/api/kiosco/atender-turno', json={'id': turno_id})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True


class TestKioscoConfig:
    """Tests for GET/PUT /api/kiosco/config."""

    def test_config_get(self, client):
        resp = client.get('/api/kiosco/config')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'branding' in data
        assert 'consent' in data
        assert 'whatsapp' in data
        assert 'lang' in data

    def test_config_put_requires_auth(self, client):
        resp = client.put('/api/kiosco/config', json={'lang': 'en'})
        assert resp.status_code == 401

    def test_config_put_authed(self, auth_client):
        resp = auth_client.put('/api/kiosco/config', json={
            'branding': {'clinicName': 'Test Clinic', 'primaryColor': '#FF0000'},
            'lang': 'en',
            'adminPin': '9999',
        })
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

        resp = auth_client.get('/api/kiosco/config')
        data = resp.get_json()
        assert data['branding']['clinicName'] == 'Test Clinic'
        assert data['lang'] == 'en'


class TestKioscoAnuncios:
    """Tests for GET/PUT /api/kiosco/anuncios."""

    def test_anuncios_get(self, client):
        resp = client.get('/api/kiosco/anuncios')
        assert resp.status_code == 200
        assert 'anuncios' in resp.get_json()

    def test_anuncios_put_requires_auth(self, client):
        resp = client.put('/api/kiosco/anuncios', json={'anuncios': []})
        assert resp.status_code == 401

    def test_anuncios_put_authed(self, auth_client):
        resp = auth_client.put('/api/kiosco/anuncios', json={
            'anuncios': [
                {'titulo': 'Test Ad', 'descripcion': 'Desc', 'media_type': 'none', 'activo': True},
            ]
        })
        assert resp.status_code == 200

        resp = auth_client.get('/api/kiosco/anuncios')
        anuncios = resp.get_json()['anuncios']
        assert len(anuncios) == 1
        assert anuncios[0]['titulo'] == 'Test Ad'


class TestKioscoServicios:
    """Tests for GET/PUT /api/kiosco/servicios."""

    def test_servicios_get(self, client):
        resp = client.get('/api/kiosco/servicios')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'servicios' in data
        assert len(data['servicios']) >= 6

    def test_servicios_put_requires_auth(self, client):
        resp = client.put('/api/kiosco/servicios', json={'servicios': []})
        assert resp.status_code == 401

    def test_servicios_put_authed(self, auth_client):
        resp = auth_client.put('/api/kiosco/servicios', json={
            'servicios': [
                {'codigo': 'test1', 'nombre': 'Test Service', 'icono': '🔬', 'activo': True},
            ]
        })
        assert resp.status_code == 200

        resp = auth_client.get('/api/kiosco/servicios')
        servicios = resp.get_json()['servicios']
        assert len(servicios) == 1
        assert servicios[0]['nombre'] == 'Test Service'


class TestAuditLog:
    """Tests for GET /api/audit."""

    def test_audit_requires_auth(self, client):
        resp = client.get('/api/audit')
        assert resp.status_code == 401

    def test_audit_authed(self, auth_client):
        resp = auth_client.get('/api/audit')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'logs' in data
        assert isinstance(data['logs'], list)

    def test_audit_filter_by_entidad(self, auth_client):
        resp = auth_client.get('/api/audit?entidad=admisiones&limit=10')
        assert resp.status_code == 200


class TestNewStaticRoutes:
    """Tests for new static page routes."""

    def test_kiosco_tv_page(self, client):
        resp = client.get('/kiosco/tv')
        assert resp.status_code == 200

    def test_kiosco_admin_page(self, client):
        resp = client.get('/kiosco/admin')
        assert resp.status_code == 200
