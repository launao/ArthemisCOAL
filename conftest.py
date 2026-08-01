"""
conftest.py — Fixtures compartidos para la suite de pruebas de ArthemisCOAL.

Configura:
  - Aplicación Flask en modo test (SQLite en memoria temporal)
  - Clientes autenticados por cada rol
  - Helpers para crear datos de prueba
"""

import os
import sys
import json
import pytest
import tempfile

# Variables de entorno ANTES de importar app
os.environ['SECRET_KEY'] = 'test-secret-key-12345'
os.environ['FLASK_ENV'] = 'testing'
os.environ['DATABASE_URL'] = ''
os.environ['PASS_SALT'] = 'test_salt_2026'
os.environ['CORS_ORIGINS'] = 'http://localhost:5050,https://allowed-origin.example.com'

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope='session')
def app():
    """Crea la aplicación Flask con SQLite temporal."""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')

    import core
    import app as app_module

    # Force SQLite
    core.USE_PG = False
    core.DATABASE_URL = ''

    import sqlite3
    _original_get_db = core.get_db

    def _test_get_db():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn, 'sqlite'

    core.get_db = _test_get_db

    flask_app = app_module.app
    flask_app.config['TESTING'] = True
    flask_app.config['SECRET_KEY'] = 'test-secret-key-12345'
    flask_app.config['SESSION_COOKIE_SECURE'] = False

    # Init DB
    app_module.init_db()
    core.seed_auth()

    yield flask_app

    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    """Cliente de prueba sin autenticación."""
    with app.test_client() as c:
        yield c


@pytest.fixture
def admin_password():
    return 'AdminTest1234'


@pytest.fixture
def _setup_admin(app, admin_password):
    """Configura el usuario admin con contraseña conocida."""
    import core
    conn, db = core.get_db()
    cur = conn.cursor()
    hashed = core.hash_pass(admin_password)
    cur.execute("UPDATE usuarios SET pass_hash=? WHERE usuario='admin'", (hashed,))
    conn.commit()
    cur.close()
    conn.close()


@pytest.fixture
def auth_client(app, client, admin_password, _setup_admin):
    """Cliente autenticado como admin/superadmin."""
    resp = client.post('/api/auth/login', json={
        'usuario': 'admin',
        'password': admin_password,
    })
    assert resp.status_code == 200, f"Login admin falló: {resp.get_json()}"
    return client


def _create_role_user(app, usuario, nombre, rol_nombre, password):
    """Helper: crea un usuario con un rol específico."""
    import core
    conn, db = core.get_db()
    cur = conn.cursor()

    rol = core.row(cur, "SELECT id FROM roles WHERE nombre=?", (rol_nombre,))
    if not rol:
        permisos = _permisos_por_rol(rol_nombre)
        cur.execute(
            "INSERT INTO roles(nombre, descripcion, permisos, es_sistema) VALUES(?,?,?,0)",
            (rol_nombre, f'Rol {rol_nombre}', json.dumps(permisos)))
        conn.commit()
        rol = core.row(cur, "SELECT id FROM roles WHERE nombre=?", (rol_nombre,))

    existing = core.row(cur, "SELECT id FROM usuarios WHERE usuario=?", (usuario,))
    if not existing:
        cur.execute(
            "INSERT INTO usuarios(usuario, nombre, email, pass_hash, rol_id, rol_nombre, activo) VALUES(?,?,?,?,?,?,1)",
            (usuario, nombre, f'{usuario}@test.co', core.hash_pass(password),
             rol['id'], rol_nombre))
        conn.commit()

    cur.close()
    conn.close()


def _permisos_por_rol(rol_nombre):
    mapa = {
        'Superadmin': ['kiosco', 'admisiones', 'historia_clinica', 'facturacion',
                       'inventario', 'agendamiento', 'reportes', 'superadmin'],
        'Médico': ['historia_clinica', 'agendamiento'],
        'Recepción': ['kiosco', 'admisiones', 'facturacion'],
        'Facturación': ['facturacion', 'reportes'],
    }
    return mapa.get(rol_nombre, [])


@pytest.fixture
def recepcion_client(app, client):
    """Cliente autenticado como recepción."""
    pw = 'Recepcion1234'
    _create_role_user(app, 'recep_test', 'Recepción Test', 'Recepción', pw)
    with app.test_client() as c:
        resp = c.post('/api/auth/login', json={'usuario': 'recep_test', 'password': pw})
        assert resp.status_code == 200
        yield c


# ── Helpers ──

def create_test_patient(auth_client, num_doc=None, nombres='Test', apellidos='Paciente'):
    """Crea un paciente de prueba."""
    import secrets as sec
    if num_doc is None:
        num_doc = str(sec.randbelow(9000000000) + 1000000000)
    resp = auth_client.post('/api/pacientes', json={
        'tipo_doc': 'CC',
        'num_doc': num_doc,
        'nombres': nombres,
        'apellidos': apellidos,
        'fecha_nacimiento': '1990-01-15',
        'genero': 'M',
        'celular': '3001234567',
        'eps': 'Sanitas',
        'tipo_afiliado': 'Contributivo',
    })
    return resp.get_json(), num_doc
