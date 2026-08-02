#!/usr/bin/env python3
"""
Arthemis Health — Flujo de Demo E2E Completo
Uses EXACT field names from the actual API contracts.
"""
import requests, json, sys

BASE = 'http://127.0.0.1:5050'
S = requests.Session()
PASS = 0
FAIL = 0

def step(name, ok, detail=''):
    global PASS, FAIL
    icon = '✅' if ok else '❌'
    if ok: PASS += 1
    else: FAIL += 1
    print(f'  {icon} {name}' + (f' — {detail}' if detail else ''))
    if not ok and detail:
        pass  # detail already shown
    return ok

def J(r):
    try: return r.json()
    except: return {}

print('='*60)
print('ARTHEMIS HEALTH — FLUJO DE DEMO E2E')
print('='*60)

# ─── FASE 1: AUTH + RBAC ───
print('\n📋 FASE 1: Autenticación y RBAC')

r = S.post(f'{BASE}/api/auth/login', json={'usuario':'admin','password':'admin123'})
step('Login superadmin', r.status_code == 200 and J(r).get('success'), J(r).get('rol',''))

r = S.get(f'{BASE}/api/auth/me')
step('Auth me', r.status_code == 200, f"permisos: {J(r).get('permisos')}")

for name, url in [('Stats','/api/admin/stats'), ('Health','/api/admin/health-check'),
                   ('Audit','/api/admin/audit'), ('Catálogo permisos','/api/admin/permisos-catalogo'),
                   ('Roles','/api/admin/roles'), ('Usuarios','/api/admin/usuarios')]:
    r = S.get(f'{BASE}{url}')
    step(name, r.status_code == 200)

# Crear rol test
r = S.post(f'{BASE}/api/admin/roles', json={'nombre':'DemoRole','permisos':['kiosco']})
test_role_id = J(r).get('rol',{}).get('id')
step('Crear rol', r.status_code in (200,201) and test_role_id, f"id={test_role_id}")

# Crear usuario test (needs uppercase+lowercase+digit, >=8 chars)
import time
_uname = f'demo_{int(time.time())%10000}'
r = S.post(f'{BASE}/api/admin/usuarios', json={
    'usuario':_uname,'password':'DemoPass1','nombre':'Dr Demo','rol_id': test_role_id or 1
})
test_user_id = J(r).get('usuario',{}).get('id')
step('Crear usuario', r.status_code in (200,201) and test_user_id, f"id={test_user_id} usr={_uname}")

# Get puestos (numeric IDs)
r = S.get(f'{BASE}/api/atencion/puestos')
puestos = J(r).get('puestos',[])
PID = lambda t: next((p['id'] for p in puestos if p['tipo']==t), None)
S.post(f'{BASE}/api/auth/logout')

# ─── FASE 2: KIOSCO ───
print('\n📋 FASE 2: Kiosco — Paciente llega')

r = S.post(f'{BASE}/api/kiosco/anuncio', json={
    'doc_tipo':'CC','doc_num':'5556667771','nombre':'Ana Torres Demo',
    'motivo':'Dolor abdominal agudo','servicio':'urgencias','prioridad':'alta'
})
d = J(r)
step('Anuncio kiosco', r.status_code == 200 and d.get('turno'), f"turno={d.get('turno')} id={d.get('id')}")
ADM_ID = d.get('id')  # DB id, not id_adm

r = S.get(f'{BASE}/api/kiosco/turnos-hoy')
step('Turnos hoy', r.status_code == 200, f"total={len(J(r))}")

r = S.get(f'{BASE}/api/kiosco/cola')
step('Cola kiosco', r.status_code == 200)

# ─── FASE 3: TRIAGE ───
print('\n📋 FASE 3: Triage — Enfermera')

S.post(f'{BASE}/api/auth/login', json={'usuario':'admin','password':'admin123'})
r = S.post(f'{BASE}/api/atencion/seleccionar-puesto', json={'puesto_id': PID('triage')})
step('Puesto triage', r.status_code == 200)

r = S.get(f'{BASE}/api/atencion/cola')
step('Cola triage', r.status_code == 200, f"total={J(r).get('total',0)}")

r = S.post(f'{BASE}/api/atencion/siguiente')
pac = J(r).get('paciente',{})
step('Llamar siguiente', r.status_code == 200, f"turno={pac.get('turno')} id={pac.get('id')}")
SIG_ID = pac.get('id')  # This is the admision.id

# Triage accion: fields at TOP level, 'id' not 'admision_id', 'nivel' not 'clasificacion'
r = S.post(f'{BASE}/api/atencion/accion', json={
    'id': SIG_ID,
    'nivel': 'III',
    'ta_sistolica': 120, 'ta_diastolica': 80,
    'fc': 80, 'fr': 18, 'temperatura': 36.5, 'spo2': 98,
    'eva_dolor': 6, 'dolor_localizacion': 'FID',
    'motivo_consulta': 'Dolor abdominal agudo en fosa ilíaca derecha',
    'alergias': 'Ninguna conocida',
    'notas_enfermeria': 'Paciente consciente, orientado, hidratado'
})
step('Completar triage', r.status_code == 200, f"{J(r).get('msg','')[:50]}")

r = S.get(f'{BASE}/api/atencion/triage-clinico/{SIG_ID}')
tc = J(r).get('triage',{}) or {}
step('Triage guardado', r.status_code == 200, f"nivel={tc.get('nivel_asignado','?')}")

r = S.get(f'{BASE}/api/atencion/triage-config')
step('Triage config', r.status_code == 200)

# ─── FASE 4: ADMISIONES ───
print('\n📋 FASE 4: Admisiones — Admisionista')

r = S.post(f'{BASE}/api/atencion/seleccionar-puesto', json={'puesto_id': PID('admisiones')})
step('Puesto admisiones', r.status_code == 200)

r = S.post(f'{BASE}/api/atencion/siguiente')
pac = J(r).get('paciente',{})
step('Llamar paciente', r.status_code == 200, f"turno={pac.get('turno')} id={pac.get('id')}")
ADM_STEP = pac.get('id', SIG_ID)

# Search paciente (param is 'num_doc', not 'q')
r = S.get(f'{BASE}/api/admisiones/buscar-paciente?num_doc=5556667771')
found = J(r)
step('Buscar paciente', r.status_code == 200, f"encontrado={found.get('encontrado')}")
pac_id = found.get('paciente',{}).get('id') if found.get('encontrado') else None

if not pac_id:
    # Create: fields are num_doc, nombres, apellidos (not doc_num, nombre, apellido)
    r = S.post(f'{BASE}/api/admisiones/crear-paciente', json={
        'tipo_doc':'CC','num_doc':'5556667771','nombres':'Ana','apellidos':'Torres Demo',
        'fecha_nacimiento':'1985-03-20','genero':'F','celular':'3109876543',
        'direccion':'Calle 45 #12-34','eps':'Nueva EPS','tipo_afiliado':'Contributivo'
    })
    d = J(r)
    pac_id = d.get('id') or d.get('paciente',{}).get('id')
    step('Crear paciente', r.status_code in (200,201), f"id={pac_id}")

# Vincular
r = S.put(f'{BASE}/api/admisiones/vincular-paciente', json={
    'admision_id': ADM_STEP, 'paciente_id': pac_id
})
step('Vincular paciente', r.status_code == 200, f"{J(r).get('msg','')[:40]}")

# Validar pagador
r = S.post(f'{BASE}/api/admisiones/validar-pagador', json={
    'admision_id': ADM_STEP, 'tipo_pagador': 'eps_contributivo',
    'entidad': 'Nueva EPS', 'codigo_entidad': '003',
    'contrato': 'CTR-2024-002'
})
step('Validar pagador', r.status_code == 200, f"{J(r).get('msg','')[:40]}")

# Copago
r = S.get(f'{BASE}/api/admisiones/calcular-copago?admision_id={ADM_STEP}&estrato=3&tipo_atencion=urgencias')
step('Calcular copago', r.status_code == 200)

# Completar admisión
r = S.post(f'{BASE}/api/admisiones/completar', json={
    'admision_id': ADM_STEP, 'consentimiento': True
})
step('Completar admisión', r.status_code == 200, f"{J(r).get('msg','')[:50]}")

# Advance state via accion
r = S.post(f'{BASE}/api/atencion/accion', json={'id': ADM_STEP})
step('Avanzar estado admisión', r.status_code == 200)

# Timeline
r = S.get(f'{BASE}/api/admisiones/timeline/{ADM_STEP}')
step('Timeline', r.status_code == 200)

# Detalle
r = S.get(f'{BASE}/api/admisiones/detalle/{ADM_STEP}')
step('Detalle admisión', r.status_code == 200)

# ─── FASE 5: CONSULTA MÉDICA ───
print('\n📋 FASE 5: Consulta Médica')

r = S.post(f'{BASE}/api/atencion/seleccionar-puesto', json={'puesto_id': PID('consultorio')})
step('Puesto consultorio', r.status_code == 200)

r = S.post(f'{BASE}/api/atencion/siguiente')
pac = J(r).get('paciente',{})
step('Llamar consulta', r.status_code == 200, f"turno={pac.get('turno')}")
ADM_CONS = pac.get('id', ADM_STEP)

r = S.post(f'{BASE}/api/hc/abrir', json={'admision_id': ADM_CONS})
d = J(r)
hc_id = d.get('hc_id')
# May return 200 (new) or 409 (already open) with existing hc_id
if not hc_id and d.get('error'):
    # HC already exists for this admission — get it
    r2 = S.get(f'{BASE}/api/hc/por-admision/{ADM_CONS}')
    hc_id = J(r2).get('id') or J(r2).get('hc_id')
step('Abrir/obtener HC', hc_id is not None, f"hc_id={hc_id}")

if hc_id:
    r = S.get(f'{BASE}/api/hc/{hc_id}')
    step('Leer HC', r.status_code == 200, f"estado={J(r).get('estado')}")

    r = S.get(f'{BASE}/api/hc/por-admision/{ADM_CONS}')
    step('HC por admisión', r.status_code == 200)

    r = S.post(f'{BASE}/api/hc/{hc_id}/evolucion', json={
        'enfermedad_actual': 'Dolor abdominal FID, 24h, progresivo',
        'examen_fisico': 'McBurney (+), Blumberg (+), defensa muscular',
        'signos_vitales_json': json.dumps({'fc':80,'fr':18,'tas':120,'tad':80,'temp':36.5,'spo2':98}),
        'analisis': 'Apendicitis aguda probable',
        'plan_terapeutico': 'Labs + eco + valoración cirugía',
        'cod_cie10': 'K35'
    })
    step('Evolución', r.status_code in (200,201))

    r = S.get(f'{BASE}/api/hc/{hc_id}/evoluciones')
    step('Listar evoluciones', r.status_code == 200)

    r = S.get(f'{BASE}/api/hc/campos-config')
    step('Campos HC config', r.status_code == 200)

    # Antecedentes
    r = S.post(f'{BASE}/api/hc/antecedentes', json={
        'paciente_id': pac_id, 'tipo': 'quirurgico',
        'descripcion': 'Amigdalectomía 2010'
    })
    step('Agregar antecedente', r.status_code in (200,201))

    r = S.get(f'{BASE}/api/hc/antecedentes/{pac_id}')
    step('Ver antecedentes', r.status_code == 200)

# ─── FASE 6: ÓRDENES ───
print('\n📋 FASE 6: Órdenes Médicas')

orden_lab_id = None
if hc_id:
    r = S.post(f'{BASE}/api/ordenes/crear', json={
        'hc_id': hc_id, 'admision_id': ADM_CONS, 'paciente_id': pac_id,
        'tipo_orden': 'laboratorio', 'nombre_estudio': 'Hemograma completo',
        'cod_cups': '902210', 'prioridad': 'urgente'
    })
    orden_lab_id = J(r).get('orden_id')
    step('Orden lab', r.status_code in (200,201), f"id={orden_lab_id}")

    r = S.post(f'{BASE}/api/ordenes/crear', json={
        'hc_id': hc_id, 'admision_id': ADM_CONS, 'paciente_id': pac_id,
        'tipo_orden': 'imagenologia', 'nombre_estudio': 'Eco abdominal',
        'cod_cups': '881601', 'prioridad': 'urgente'
    })
    step('Orden imagen', r.status_code in (200,201), f"id={J(r).get('orden_id')}")

    r = S.get(f'{BASE}/api/ordenes/por-hc/{hc_id}')
    step('Órdenes por HC', r.status_code == 200)

    r = S.get(f'{BASE}/api/ordenes/estadisticas')
    step('Estadísticas', r.status_code == 200)

# ─── FASE 7: LABORATORIO ───
print('\n📋 FASE 7: Laboratorio')

if orden_lab_id:
    r = S.get(f'{BASE}/api/ordenes/cola/laboratorio')
    step('Cola lab', r.status_code == 200)

    r = S.put(f'{BASE}/api/ordenes/{orden_lab_id}/aceptar')
    step('Aceptar', r.status_code == 200)

    r = S.put(f'{BASE}/api/ordenes/{orden_lab_id}/en-proceso')
    step('En proceso', r.status_code == 200)

    r = S.post(f'{BASE}/api/ordenes/{orden_lab_id}/resultado', json={
        'resultados': [
            {'parametro':'Hemoglobina','valor':'14.5','unidad':'g/dL','rango_referencia':'12.0-16.0','observaciones':'Normal'},
            {'parametro':'Leucocitos','valor':'12000','unidad':'/uL','rango_referencia':'4500-11000','fuera_rango':True}
        ]
    })
    step('Resultado', r.status_code in (200,201))

    r = S.put(f'{BASE}/api/ordenes/{orden_lab_id}/validar')
    step('Validar', r.status_code == 200)

    r = S.get(f'{BASE}/api/ordenes/{orden_lab_id}/resultados')
    step('Ver resultados', r.status_code == 200)

# ─── FASE 8: INTERCONSULTA ───
print('\n📋 FASE 8: Interconsulta')

ic_id = None
if hc_id:
    r = S.post(f'{BASE}/api/interconsultas/solicitar', json={
        'hc_id': hc_id, 'admision_id': ADM_CONS, 'paciente_id': pac_id,
        'especialidad_solicitada': 'cirugia_general',
        'motivo': 'Apendicitis aguda', 'hallazgos': 'McBurney (+)', 'prioridad': 'urgente'
    })
    ic_id = J(r).get('interconsulta_id')
    step('Solicitar', r.status_code in (200,201), f"id={ic_id}")

    r = S.get(f'{BASE}/api/interconsultas/pendientes')
    step('Pendientes', r.status_code == 200)

    r = S.get(f'{BASE}/api/interconsultas/por-hc/{hc_id}')
    step('Por HC', r.status_code == 200)

    if ic_id:
        r = S.put(f'{BASE}/api/interconsultas/{ic_id}/aceptar')
        step('Aceptar', r.status_code == 200)

        r = S.put(f'{BASE}/api/interconsultas/{ic_id}/responder', json={
            'respuesta': 'Apendicitis aguda. Indicar cirugía laparoscópica.',
            'diagnostico': 'K35 Apendicitis aguda',
            'recomendaciones': 'Preparar para cirugía.'
        })
        step('Responder', r.status_code == 200)

# ─── FASE 9: EGRESO ───
print('\n📋 FASE 9: Egreso')

if hc_id:
    r = S.put(f'{BASE}/api/hc/{hc_id}/diagnostico-egreso', json={
        'cod_cie10_egreso':'K35.9','diagnosticos_relacionados':['K35','R10.3'],
        'condicion_egreso':'vivo','destino_egreso':'cirugia'
    })
    step('Dx egreso', r.status_code == 200)

    r = S.put(f'{BASE}/api/hc/{hc_id}/cerrar', json={})
    step('Cerrar HC', r.status_code == 200, f"{J(r).get('msg','')[:40]}")

    r = S.get(f'{BASE}/api/hc/{hc_id}/resumen')
    step('Resumen HC', r.status_code == 200)

# ─── FASE 10: FACTURACIÓN ───
print('\n📋 FASE 10: Facturación')

r = S.post(f'{BASE}/api/facturacion/generar/{ADM_CONS}')
step('Generar pre-factura', r.status_code == 200, f"{J(r).get('msg','')[:40]}")

r = S.get(f'{BASE}/api/facturacion/pre-factura/{ADM_CONS}')
pf_id = J(r).get('id')
step('Ver pre-factura', r.status_code == 200, f"pf_id={pf_id}")

if pf_id:
    r = S.post(f'{BASE}/api/facturacion/item', json={
        'pre_factura_id':pf_id,'tipo_servicio':'procedimiento',
        'cod_cups':'470100','descripcion':'Apendicectomía','cantidad':1,'valor_unitario':2500000
    })
    step('Agregar item', r.status_code == 200)

    r = S.put(f'{BASE}/api/facturacion/aprobar/{pf_id}')
    step('Aprobar', r.status_code == 200)

r = S.get(f'{BASE}/api/facturacion/pendientes')
step('Pendientes', r.status_code == 200)

r = S.get(f'{BASE}/api/facturacion/resumen-diario')
step('Resumen diario', r.status_code == 200)

r = S.post(f'{BASE}/api/facturacion/rips/{ADM_CONS}')
step('RIPS JSON', r.status_code == 200)

# ─── FASE 11: IMPRESIÓN ───
print('\n📋 FASE 11: Impresión')

if hc_id:
    for name, url in [('HC',f'/api/impresion/hc/{hc_id}'),('Receta',f'/api/impresion/receta/{hc_id}'),
                       ('Plan',f'/api/impresion/plan-cuidado/{hc_id}')]:
        r = S.get(f'{BASE}{url}')
        step(f'Imprimir {name}', r.status_code == 200)

r = S.get(f'{BASE}/api/impresion/constancia/{ADM_CONS}')
step('Constancia', r.status_code == 200)

# ─── FASE 12: DASHBOARDS ───
print('\n📋 FASE 12: Dashboards')

for name, url in [('Médico','/api/dashboard/medico'),('Enfermería','/api/dashboard/enfermeria'),
                   ('Admisiones','/api/dashboard/admisiones'),('Financiero','/api/dashboard/financiero'),
                   ('Adm público','/api/admisiones/dashboard')]:
    r = S.get(f'{BASE}{url}')
    step(name, r.status_code == 200)

# ─── FASE 13: RBAC ───
print('\n📋 FASE 13: RBAC')

S.post(f'{BASE}/api/auth/logout')
S2 = requests.Session()
r = S2.post(f'{BASE}/api/auth/login', json={'usuario':_uname,'password':'DemoPass1'})
if J(r).get('success'):
    step('Login limitado', True)
    for name, url, expect in [('Stats','/api/admin/stats',403),('Health','/api/admin/health-check',403),
                                ('Roles','/api/admin/roles',403)]:
        r = S2.get(f'{BASE}{url}')
        step(f'RBAC: {name} denegado', r.status_code == expect)
    S2.post(f'{BASE}/api/auth/logout')

# ─── FASE 14: PÁGINAS ───
print('\n📋 FASE 14: Páginas HTML (14)')

S.post(f'{BASE}/api/auth/login', json={'usuario':'admin','password':'admin123'})
for path in ['/','/kiosco','/kiosco/tv','/kiosco/admin','/atencion/triage','/atencion/admisiones',
             '/atencion/consulta','/admisiones/dashboard','/laboratorio','/interconsultas',
             '/facturacion','/admin/dashboard','/admin/usuarios','/dashboard']:
    r = S.get(f'{BASE}{path}')
    step(path, r.status_code == 200)

# ─── FASE 15: CAMPOS ───
print('\n📋 FASE 15: Campos Admin')

for mod in ['hc','triage']:
    r = S.get(f'{BASE}/api/admin/campos/{mod}')
    step(f'Campos {mod}', r.status_code == 200)

# ─── LIMPIEZA ───
print('\n📋 Limpieza')
if test_user_id:
    S.delete(f'{BASE}/api/admin/usuarios/{test_user_id}')
if test_role_id:
    S.delete(f'{BASE}/api/admin/roles/{test_role_id}')
S.post(f'{BASE}/api/auth/logout')

# ─── RESUMEN ───
print('\n' + '='*60)
print(f'RESULTADO: {PASS} ✅  |  {FAIL} ❌')
print(f'TOTAL: {PASS+FAIL} tests  |  TASA: {PASS/(PASS+FAIL)*100:.1f}%')
print('='*60)
sys.exit(0 if FAIL == 0 else 1)
