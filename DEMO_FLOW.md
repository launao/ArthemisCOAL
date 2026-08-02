# Arthemis Health — Flujo de Demo

## Credenciales

| Usuario | Contraseña | Rol |
|---------|-----------|-----|
| admin | admin123 | Superadmin |

## Flujo completo (15 fases, ~20 min)

---

### FASE 1: Kiosco — Paciente llega (sin login)

1. Abrir `/kiosco`
2. Ingresar: CC `1098765432`, Nombre: "Carlos Méndez", Motivo: "Dolor torácico"
3. Se genera turno (ej: PS1) y aparece en pantalla
4. Abrir `/kiosco/tv` en otra pestaña — se ve el turno en la pantalla de sala de espera
5. **Lo que se muestra:** Registro público sin autenticación, turnos en tiempo real via SSE

### FASE 2: Login y Selección de Puesto

1. Abrir `/atencion/triage`
2. Login: admin / admin123
3. Seleccionar puesto: "Triage 1" (tipo: triage)
4. **Lo que se muestra:** Sistema de autenticación, selección de puesto por tipo

### FASE 3: Triage — Enfermera

1. En la vista de triage, ver la cola de pacientes esperando
2. Click "Llamar siguiente" — el sistema llama al siguiente en la cola
3. Llenar triage clínico:
   - Nivel: III (Urgencia)
   - Signos vitales: TA 120/80, FC 80, FR 18, Temp 36.5, SpO2 98%
   - EVA dolor: 6/10, Localización: Torácico
   - Motivo: "Dolor torácico opresivo"
4. Click "Completar Triage"
5. **Lo que se muestra:** Triage completo Resolución 5596/2015, Glasgow, discriminadores, signos vitales

### FASE 4: Admisiones — Admisionista

1. Abrir `/atencion/admisiones`, seleccionar puesto "Admisiones 1"
2. Llamar siguiente — llega el paciente ya triageado
3. **Buscar paciente:** ingresando número de documento
4. Si no existe, **crear paciente** con datos demográficos completos
5. **Vincular paciente** a la admisión
6. **Validar pagador:** tipo EPS Contributivo, entidad, contrato, código
7. **Calcular copago** según estrato y tipo de atención
8. **Completar admisión** (requiere paciente vinculado + pagador validado)
9. **Lo que se muestra:** Flujo completo de admisiones colombiano, copago por Res. 5596, validación de pagador

### FASE 5: Consulta Médica

1. Abrir `/atencion/consulta`, seleccionar puesto "Consultorio 1"
2. Llamar siguiente — llega paciente admitido
3. **Abrir Historia Clínica** — se crea HC vinculada a la admisión
4. **Ver datos del triage** (signos vitales, nivel, notas enfermería)
5. **Agregar evolución médica:**
   - Enfermedad actual, examen físico, análisis
   - Signos vitales, CIE-10, plan terapéutico
6. **Agregar antecedentes** del paciente
7. **Lo que se muestra:** HC electrónica según normativa, evoluciones, CIE-10

### FASE 6: Órdenes Médicas

1. Desde la consulta, tab "Órdenes"
2. **Crear orden de laboratorio:** Hemograma, CUPS 902210, prioridad urgente
3. **Crear orden de imágenes:** Eco abdominal, CUPS 881601
4. Ver lista de órdenes por HC
5. **Lo que se muestra:** Órdenes con CUPS, prioridad, trazabilidad completa

### FASE 7: Laboratorio

1. Abrir `/laboratorio`
2. Ver cola de laboratorio con órdenes pendientes
3. **Aceptar** la orden
4. Marcar **en proceso**
5. **Registrar resultados:** parámetro, valor, unidad, rango de referencia
6. **Validar resultados** — queda disponible para el médico
7. **Lo que se muestra:** Worklist de laboratorio completa, resultados validados

### FASE 8: Interconsulta

1. Desde la consulta, tab "Interconsultas"
2. **Solicitar interconsulta** a Cirugía General, motivo clínico
3. Abrir `/interconsultas` — ver pendientes
4. **Aceptar** la interconsulta
5. **Responder** con diagnóstico y recomendaciones
6. **Lo que se muestra:** Flujo completo de interconsultas entre especialidades

### FASE 9: Egreso

1. Volver a la consulta
2. Tab "Egreso"
3. **Diagnóstico de egreso:** CIE-10, condición, destino
4. **Cerrar HC** — automáticamente genera pre-factura
5. **Ver resumen** completo de la HC (incluye triage, evoluciones, órdenes, interconsultas)
6. **Lo que se muestra:** Cierre normativo, resumen integral

### FASE 10: Facturación

1. Abrir `/facturacion`
2. **Generar pre-factura** para la admisión
3. Ver detalle: items auto-generados desde órdenes
4. **Agregar item manual:** procedimiento, CUPS, valor
5. **Aprobar factura**
6. **Generar RIPS JSON** (Resolución 2275/2023) — descargable
7. Ver **resumen diario** y **pendientes**
8. **Lo que se muestra:** Facturación por tipo pagador, RIPS JSON normativo

### FASE 11: Impresión

1. Desde facturación o consulta
2. **Imprimir HC** — documento HTML completo
3. **Imprimir receta** — formato de prescripción
4. **Imprimir plan de cuidado**
5. **Imprimir constancia de atención**
6. **Lo que se muestra:** 4 tipos de documentos clínicos imprimibles

### FASE 12: Dashboard Superadmin

1. Abrir `/admin/dashboard`
2. **Overview:** estadísticas en tiempo real — admisiones hoy, HC abiertas, órdenes, facturación
3. **Endpoints:** lista de los 104+ endpoints registrados por módulo
4. **Alertas:** generadas automáticamente por umbrales
5. **Cuentas activas** y resumen de roles
6. **Audit trail** con todas las acciones del sistema
7. Tabs de dashboards operativos: Médico, Enfermería, Admisiones, Finanzas
8. **Lo que se muestra:** Monitoreo completo, visibilidad de todo el sistema

### FASE 13: Gestión de Usuarios y Roles

1. Abrir `/admin/usuarios`
2. Tab "Usuarios": crear/editar/desactivar usuarios, asignar roles
3. Tab "Roles": ver los 15 roles con permisos granulares (28 permisos)
4. Tab "Campos HC": configurar campos de historia clínica
5. Tab "Campos Triage": configurar campos del formulario de triage
6. **Lo que se muestra:** RBAC completo, configuración dinámica de formularios

### FASE 14: Control de Acceso (RBAC)

1. Crear usuario de prueba con rol limitado (ej: "Cajero")
2. Login con ese usuario
3. Intentar acceder a `/api/admin/stats` → **403 Forbidden**
4. Intentar acceder a `/api/admin/roles` → **403 Forbidden**
5. **Lo que se muestra:** Control de acceso granular, cada rol ve solo lo que le corresponde

### FASE 15: Kiosco Admin

1. Abrir `/kiosco/admin`
2. Configurar servicios, anuncios del carrusel
3. Ver audit trail del kiosco
4. **Lo que se muestra:** Panel de administración completo del kiosco

---

## Resumen de Módulos

| Módulo | Endpoints | Vistas |
|--------|-----------|--------|
| Kiosco | 12 | kiosco, kiosco-tv, kiosco-admin |
| Atención | 14 | atencion-triage |
| Admisiones | 13 | atencion-admisiones, admisiones-dashboard |
| Historia Clínica | 13 | atencion-consulta |
| Órdenes | 10 | laboratorio |
| Interconsultas | 7 | interconsultas |
| Facturación | 9 | facturacion |
| Impresión | 4 | (integrado en consulta/facturación) |
| RBAC/Admin | 19 | admin-dashboard, admin-usuarios |
| **Total** | **104+** | **14 vistas** |

## Normativa Colombiana Implementada

- Ley 100/1993 — Sistema General de Seguridad Social
- Ley 1581/2012 — Habeas Data
- Ley 1751/2015 — Derecho Fundamental a la Salud
- Resolución 5596/2015 — Triage de Urgencias (5 niveles)
- Resolución 2275/2023 — RIPS JSON
- Resolución 1888/2025 — Historia Clínica Electrónica
- Circular 048/2025 — Interoperabilidad

## Test Automatizado

```bash
python test_demo_e2e.py
```

89 tests, 15 fases, flujo completo end-to-end. Resultado: **100% PASS**.
