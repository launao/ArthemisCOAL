#!/usr/bin/env python3
"""Integration test: full urgencias admission flow (15 steps)."""

import requests, json, sys, os

BASE = "http://127.0.0.1:5050"
S = requests.Session()
PASS = os.getenv("ADMIN_PASSWORD", "admin123")

def step(n, desc):
    print(f"\n{'='*60}\nStep {n}: {desc}\n{'='*60}")

def check(resp, label=""):
    print(f"  {label} → {resp.status_code}")
    try:
        d = resp.json()
        print(f"  {json.dumps(d, ensure_ascii=False)[:400]}")
        return d
    except:
        print(f"  (no JSON) {resp.text[:200]}")
        return None

def ok(d):
    return d and (d.get("ok") or d.get("success"))

# ── 1. Login ──
step(1, "Login como admin")
d = check(S.post(f"{BASE}/api/auth/login", json={"usuario": "admin", "password": PASS}), "login")
assert ok(d), f"Login failed: {d}"

# ── 2. List puestos (use seed data: T1=triage, A1=admisiones) ──
step(2, "Listar puestos de atención")
puestos = check(S.get(f"{BASE}/api/atencion/puestos"), "listar puestos")
assert puestos and puestos.get('puestos'), "No puestos found"
triaje_puesto_id = None
admisiones_puesto_id = None
for p in puestos['puestos']:
    if p['tipo'] == 'triage' and not triaje_puesto_id:
        triaje_puesto_id = p['id']
    elif p['tipo'] == 'admisiones' and not admisiones_puesto_id:
        admisiones_puesto_id = p['id']
print(f"  triaje_puesto_id={triaje_puesto_id}, admisiones_puesto_id={admisiones_puesto_id}")
assert triaje_puesto_id and admisiones_puesto_id, "Could not find triage and admisiones puestos"

# ── 3. Select puesto triaje ──
step(3, "Seleccionar puesto triaje")
d = check(S.post(f"{BASE}/api/atencion/seleccionar-puesto", json={"puesto_id": triaje_puesto_id}), "puesto")
assert ok(d), f"Puesto failed: {d}"

# ── 4. Create kiosco turn (anuncio) ──
step(4, "Crear turno kiosco (anuncio)")
d = check(S.post(f"{BASE}/api/kiosco/anuncio", json={
    "doc_type": "CC", "doc_num": "1234567890",
    "nombre": "Juan Carlos Pérez López",
    "celular": "3001234567",
    "tipo_atencion": "sin_cita",
    "turno_tipo": "general",
    "servicio_nombre": "Urgencias",
    "habeas_data": True
}), "anuncio")
assert d and d.get("id"), f"Anuncio failed: {d}"
turno_id = d["id"]
turno_code = d.get("turno", "?")
print(f"  turno_id={turno_id}, turno={turno_code}")

# ── 5. Call next (triage pulls from kiosco queue) ──
step(5, "Llamar siguiente (triaje)")
d = check(S.post(f"{BASE}/api/atencion/siguiente"), "siguiente")
assert d and d.get("success") is not False, f"Siguiente failed: {d}"

# ── 6. Complete triage → level III (full clinical data) ──
step(6, "Completar triaje clínico nivel III")
d = check(S.post(f"{BASE}/api/atencion/accion", json={
    "id": turno_id,
    "nivel": "III",
    "motivo_consulta": "Dolor abdominal agudo de 6 horas de evolución",
    "ta_sistolica": 120, "ta_diastolica": 80,
    "fc": 88, "fr": 18, "temperatura": 37.2, "spo2": 97,
    "glucometria": 95,
    "glasgow_ocular": 4, "glasgow_verbal": 5, "glasgow_motor": 6,
    "eva_dolor": 6, "dolor_localizacion": "Cuadrante inferior derecho",
    "disc_fiebre_alta": 0, "disc_dolor_toracico": 0,
    "alergias": "Ninguna conocida",
    "notas_enfermeria": "Dolor abdominal, signos vitales estables, abdomen blando depresible"
}), "triaje clínico")
assert ok(d), f"Triaje failed: {d}"

# ── 7. Switch to admisiones puesto ──
step(7, "Cambiar a puesto admisiones")
d = check(S.post(f"{BASE}/api/atencion/seleccionar-puesto", json={"puesto_id": admisiones_puesto_id}), "puesto")
assert ok(d), f"Puesto admisiones failed: {d}"

# ── 8. Call next (admisiones pulls from triaje queue) ──
step(8, "Llamar siguiente (admisiones)")
d = check(S.post(f"{BASE}/api/atencion/siguiente"), "siguiente")
assert d and d.get("success") is not False, f"Siguiente admisiones failed: {d}"

# ── 9. Search patient ──
step(9, "Buscar paciente por documento")
d = check(S.get(f"{BASE}/api/admisiones/buscar-paciente", params={"num_doc": "1234567890", "tipo_doc": "CC"}), "buscar")
if d and d.get("encontrado") and d.get("paciente"):
    pac_id = d["paciente"]["id"]
    print(f"  Paciente encontrado: id={pac_id}")
else:
    # Create patient
    print("  Paciente no encontrado, creando...")
    d = check(S.post(f"{BASE}/api/admisiones/crear-paciente", json={
        "tipo_doc": "CC", "num_doc": "1234567890",
        "nombres": "Juan Carlos", "apellidos": "Pérez López",
        "fecha_nacimiento": "1985-03-15", "sexo": "M",
        "celular": "3001234567", "direccion": "Calle 123 #45-67, Bogotá",
        "email": "juan.perez@test.com"
    }), "crear")
    assert d and (d.get("paciente_id") or d.get("paciente",{}).get("id")), f"Create patient failed: {d}"
    pac_id = d.get("paciente_id") or d["paciente"]["id"]
    print(f"  Paciente creado: id={pac_id}")

# ── 10. Link patient to admission ──
step(10, "Vincular paciente a admisión")
d = check(S.put(f"{BASE}/api/admisiones/vincular-paciente", json={
    "admision_id": turno_id, "paciente_id": pac_id
}), "vincular")
assert ok(d), f"Vincular failed: {d}"

# ── 11. Validate pagador (EPS Contributivo) ──
step(11, "Validar pagador EPS Contributivo")
d = check(S.post(f"{BASE}/api/admisiones/validar-pagador", json={
    "admision_id": turno_id,
    "tipo_pagador": "eps_contributivo",
    "entidad_nombre": "Nueva EPS",
    "entidad_codigo": "EPS037",
    "regimen": "contributivo",
    "estado_afiliacion": "activo",
    "nivel_sisben": "",
    "grupo_ingreso": "B"
}), "pagador")
assert ok(d), f"Pagador failed: {d}"

# ── 12. Open HC ──
step(12, "Abrir historia clínica urgencias")
d = check(S.post(f"{BASE}/api/admisiones/abrir-hc", json={
    "admision_id": turno_id,
    "motivo_consulta": "Dolor abdominal agudo de 6 horas de evolución",
    "causa_atencion": "urgencia",
    "cod_cie10_ingreso": "R10.4"
}), "abrir-hc")
assert d and d.get('hc_id'), f"HC creation FAILED: {d}"
hc_id = d['hc_id']
print(f"  HC abierta: hc_id={hc_id}")

# ── 13. Get detail ──
step(13, "Obtener detalle completo")
d = check(S.get(f"{BASE}/api/admisiones/detalle/{turno_id}"), "detalle")
assert d and d.get('admision'), "Detail failed"
print(f"  Paciente: {d.get('paciente',{}).get('nombres','?')}")
print(f"  Pagador: {d.get('pagador',{}).get('tipo_pagador','?')}")
print(f"  HC motivo: {d.get('hc',{}).get('motivo_consulta','?')}")
print(f"  Timeline events: {len(d.get('timeline',[]))}")

# ── 14. Calculate copago ──
step(14, "Calcular copago")
d = check(S.get(f"{BASE}/api/admisiones/calcular-copago", params={
    "admision_id": turno_id
}), "copago")
print(f"  Copago result: aplica={d.get('aplica')}, excento={d.get('excento')}, motivo={d.get('motivo_exencion')}")

# ── 15. Complete admission ──
step(15, "Completar admisión")
d = check(S.post(f"{BASE}/api/admisiones/completar", json={
    "admision_id": turno_id
}), "completar")
assert ok(d), f"Completar failed: {d}"

print(f"\n{'='*60}")
print("ALL 15 STEPS COMPLETED SUCCESSFULLY!")
print(f"{'='*60}")
