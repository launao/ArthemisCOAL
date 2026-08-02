const { Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow,
  TableCell, WidthType, BorderStyle, AlignmentType, ShadingType, PageBreak,
  Header, Footer, ImageRun, Tab, TabStopType, TabStopPosition,
  convertInchesToTwip, LevelFormat, NumberFormat
} = require('docx');
const fs = require('fs');

// ── Colors & styles ──────────────────────────────────────────────────────────
const PRIMARY = '5147C4';
const DARK = '1C1916';
const LIGHT_BG = 'F5F3EF';
const WHITE = 'FFFFFF';
const RED = 'DC2626';
const ORANGE = 'EA580C';
const AMBER = 'D97706';
const GREEN = '16A34A';
const BLUE = '2563EB';

const FONT = 'Calibri';
const PAGE_W = 12240; // US Letter
const PAGE_H = 15840;
const MARGIN = 1440;  // 1 inch
const TABLE_W = PAGE_W - 2 * MARGIN; // 9360 DXA

function heading(text, level = HeadingLevel.HEADING_1) {
  return new Paragraph({
    heading: level,
    spacing: { before: level === HeadingLevel.HEADING_1 ? 400 : 240, after: 120 },
    children: [new TextRun({ text, font: FONT, bold: true,
      color: level === HeadingLevel.HEADING_1 ? PRIMARY : DARK,
      size: level === HeadingLevel.HEADING_1 ? 32 : level === HeadingLevel.HEADING_2 ? 26 : 22 })],
  });
}

function para(text, opts = {}) {
  return new Paragraph({
    spacing: { after: opts.afterSpacing || 120 },
    alignment: opts.align || AlignmentType.LEFT,
    children: [new TextRun({ text, font: FONT, size: opts.size || 21,
      bold: opts.bold || false, italics: opts.italics || false,
      color: opts.color || DARK })],
  });
}

function bulletPara(text, opts = {}) {
  return new Paragraph({
    spacing: { after: 60 },
    indent: { left: 720, hanging: 360 },
    children: [new TextRun({ text: `• ${text}`, font: FONT, size: opts.size || 21,
      bold: opts.bold || false, color: opts.color || DARK })],
  });
}

function cell(text, opts = {}) {
  return new TableCell({
    width: { size: opts.width || 2340, type: WidthType.DXA },
    shading: opts.header ? { type: ShadingType.CLEAR, fill: PRIMARY, color: PRIMARY } :
             opts.bg ? { type: ShadingType.CLEAR, fill: opts.bg, color: opts.bg } : undefined,
    children: [new Paragraph({
      spacing: { before: 40, after: 40 },
      alignment: opts.align || AlignmentType.LEFT,
      children: [new TextRun({ text, font: FONT, size: opts.size || 19,
        bold: opts.bold || opts.header || false,
        color: opts.header ? WHITE : (opts.color || DARK) })],
    })],
  });
}

function tableRow(cells) {
  return new TableRow({ children: cells });
}

function simpleTable(headers, rows, colWidths) {
  const w = colWidths || headers.map(() => Math.floor(TABLE_W / headers.length));
  return new Table({
    width: { size: TABLE_W, type: WidthType.DXA },
    columnWidths: w,
    rows: [
      tableRow(headers.map((h, i) => cell(h, { header: true, width: w[i], bold: true }))),
      ...rows.map(r => tableRow(r.map((c, i) => cell(c, { width: w[i] })))),
    ],
  });
}

function divider() {
  return new Paragraph({
    spacing: { before: 200, after: 200 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: PRIMARY } },
    children: [],
  });
}

// ── Document ─────────────────────────────────────────────────────────────────
const doc = new Document({
  styles: {
    default: {
      document: { run: { font: FONT, size: 21, color: DARK } },
    },
  },
  sections: [{
    properties: {
      page: { size: { width: PAGE_W, height: PAGE_H } },
      margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [new TextRun({ text: 'Arthemis Health — Diseño Módulo Admisiones Urgencias', font: FONT, size: 16, color: PRIMARY, italics: true })],
        })],
      }),
    },
    children: [

      // ══════════════════════════════════════════════════════════════════════
      // COVER
      // ══════════════════════════════════════════════════════════════════════
      new Paragraph({ spacing: { before: 2000 }, children: [] }),
      para('ARTHEMIS HEALTH', { size: 40, bold: true, color: PRIMARY, align: AlignmentType.CENTER }),
      new Paragraph({ spacing: { after: 100 }, children: [] }),
      para('Módulo de Admisiones — Urgencias', { size: 32, bold: true, align: AlignmentType.CENTER }),
      new Paragraph({ spacing: { after: 60 }, children: [] }),
      para('Documento de Diseño Técnico y Funcional', { size: 24, italics: true, color: PRIMARY, align: AlignmentType.CENTER }),
      new Paragraph({ spacing: { after: 200 }, children: [] }),
      para('Versión 1.0 — Agosto 2026', { size: 20, align: AlignmentType.CENTER, color: '666666' }),
      para('ArthemisCOAL — Sistema de Información en Salud', { size: 20, align: AlignmentType.CENTER, color: '666666' }),
      new Paragraph({ spacing: { after: 600 }, children: [] }),

      para('Marco normativo: Resolución 5596/2015 (Triage), Resolución 2275/2023 (RIPS), Resolución 1888/2025 (HCE Interoperable), Ley 1581/2012 (Habeas Data), Circular 048/2025 (Copagos)', { size: 18, italics: true, align: AlignmentType.CENTER, color: '888888' }),

      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════════════════════════════════════════════════════════════
      // 1. RESUMEN EJECUTIVO
      // ══════════════════════════════════════════════════════════════════════
      heading('1. Resumen Ejecutivo'),
      para('Este documento define la arquitectura del módulo de Admisiones para el servicio de Urgencias de Arthemis Health. El módulo conecta el flujo del paciente desde que llega al kiosco hasta su egreso, con foco en: apertura/consulta de historia clínica, validación de derechos con pagadores colombianos (EPS, ARL, SOAT, pólizas, particular), trazabilidad minuto a minuto de cada interacción, y un dashboard operativo en tiempo real.'),
      para('El alcance de esta primera versión es exclusivamente Urgencias. El módulo se diseña como pieza independiente que se conecta con los módulos existentes (Kiosco, Atención) y futuros (Historia Clínica, Facturación).'),

      divider(),

      // ══════════════════════════════════════════════════════════════════════
      // 2. FLUJO COMPLETO DEL PACIENTE EN URGENCIAS
      // ══════════════════════════════════════════════════════════════════════
      heading('2. Flujo Completo del Paciente en Urgencias'),
      para('El recorrido del paciente pasa por 6 estados internos, cada uno con trazabilidad de quién lo procesó, cuándo, y desde qué puesto:'),

      simpleTable(
        ['Estado', 'Actor', 'Acción', 'Resultado'],
        [
          ['kiosco', 'Paciente', 'Se anuncia en kiosco de urgencias', 'Turno asignado, entra a cola'],
          ['llamando (→triage)', 'Enfermera', 'Llama al siguiente desde su puesto', 'TV muestra "Diríjase a Triage 2"'],
          ['triaje', 'Enfermera', 'Evalúa y asigna nivel I-V', 'Paciente queda priorizado en cola'],
          ['llamando (→adm)', 'Admisiones', 'Llama al siguiente por prioridad', 'TV muestra "Diríjase a Admisiones 1"'],
          ['admision', 'Admisiones', 'Abre/consulta HC, valida derechos', 'Paciente admitido formalmente'],
          ['llamando (→cons)', 'Doctor', 'Llama al siguiente por prioridad', 'TV muestra "Diríjase a Consultorio 3"'],
          ['atendido', 'Doctor', 'Atiende y registra egreso', 'Atención finalizada'],
        ],
        [1400, 1400, 2800, 3760]
      ),

      new Paragraph({ spacing: { after: 200 }, children: [] }),
      para('Cada transición de estado genera un registro en audit_trail con: usuario, timestamp, IP, puesto, y detalle de la acción. Esto permite reconstruir el timeline minuto a minuto de cada paciente.'),

      divider(),

      // ══════════════════════════════════════════════════════════════════════
      // 3. ALCANCE DEL MÓDULO DE ADMISIONES
      // ══════════════════════════════════════════════════════════════════════
      heading('3. Alcance del Módulo de Admisiones'),

      heading('3.1 Funciones del Operador de Admisiones', HeadingLevel.HEADING_2),
      para('Cuando el operador de admisiones llama a un paciente, su pantalla muestra toda la información acumulada y le permite:'),
      bulletPara('Apertura de Historia Clínica — Si el paciente no existe en el sistema, crear su registro completo (datos demográficos, contacto, aseguramiento). Si ya existe, abrir su historial.'),
      bulletPara('Validación de Derechos — Verificar cobertura del paciente con su pagador (EPS contributivo/subsidiado, ARL, SOAT, póliza de salud, particular). Registrar el resultado.'),
      bulletPara('Datos de Aseguramiento — Tipo de afiliación, EPS, régimen, estado, número de autorización si aplica.'),
      bulletPara('Copago — Calcular automáticamente según la Circular 048/2025, nivel de triage, y régimen. Exento para triage I-III, menores, embarazadas, alto costo.'),
      bulletPara('Acompañante — Registrar datos del acompañante (nombre, parentesco, celular) si aplica.'),
      bulletPara('Admitir — Confirmar la admisión formal. El paciente pasa al estado "admision" y queda disponible para el doctor.'),

      heading('3.2 Funciones de Consulta', HeadingLevel.HEADING_2),
      para('Además del flujo de atención, el módulo permite:'),
      bulletPara('Consultar historia clínica de cualquier paciente por documento.'),
      bulletPara('Ver historial de admisiones anteriores del paciente.'),
      bulletPara('Ver el timeline completo de la admisión actual (minuto a minuto).'),
      bulletPara('Buscar admisiones por fecha, estado, pagador, triage.'),

      divider(),

      // ══════════════════════════════════════════════════════════════════════
      // 4. VALIDACIÓN DE DERECHOS — PAGADORES COLOMBIANOS
      // ══════════════════════════════════════════════════════════════════════
      heading('4. Validación de Derechos — Pagadores Colombianos'),
      para('La validación de derechos determina quién paga la atención. En urgencias, la atención NUNCA se condiciona al resultado de esta validación (Art. 168, Ley 100/1993), pero debe hacerse para la facturación posterior. Colombia tiene 6 tipos de pagador:'),

      simpleTable(
        ['Pagador', 'Validación', 'Copago Urgencias', 'Integración'],
        [
          ['EPS Contributivo', 'ADRES BDUA por cédula → EPS, régimen, estado', 'Triage I-III: exento. IV-V: según Circular 048/2025', 'Fase 1: manual/ADRES web. Fase 2: Make/N8N automation'],
          ['EPS Subsidiado', 'ADRES BDUA por cédula → verificar régimen subsidiado', 'Exento en todos los niveles', 'Igual que contributivo'],
          ['ARL (Accidente Trabajo)', 'Verificar con ARL empleador. Requiere: empresa, NIT, ARL', 'No aplica — cubre 100% la ARL', 'Fase 2: formulario de reporte AT + integración ARL'],
          ['SOAT (Accidente Tránsito)', 'Verificar póliza vigente con aseguradora. Placa + aseguradora', 'No aplica — cubre SOAT', 'Fase 2: consulta por placa + reporte FURIPS'],
          ['Póliza de Salud', 'Verificar con aseguradora. Número de póliza + empresa', 'Según contrato de la póliza', 'Fase 2: integración por aseguradora'],
          ['Particular', 'No requiere validación de cobertura', 'Tarifa completa (factura directa al paciente)', 'N/A'],
        ],
        [1400, 2600, 2200, 3160]
      ),

      new Paragraph({ spacing: { after: 200 }, children: [] }),

      heading('4.1 ADRES BDUA — Consulta de Afiliación', HeadingLevel.HEADING_2),
      para('La ADRES (Administradora de los Recursos del SGSS) mantiene la BDUA (Base de Datos Única de Afiliados). La consulta se hace por tipo y número de documento, y retorna:'),
      bulletPara('EPS o entidad responsable (ej: Sanitas, Nueva EPS, Sura, Mutual Ser)'),
      bulletPara('Régimen: Contributivo, Subsidiado, Especial, Excepción'),
      bulletPara('Estado de afiliación: Activo o Inactivo'),
      bulletPara('Fecha de inicio de afiliación'),

      para('Fase 1 (Demo): El operador consulta manualmente en adres.gov.co y registra el resultado en Arthemis. Fase 2 (Producción): Automatización vía Make/N8N con scraping del portal ADRES o integración con web services cuando ADRES los habilite para IPS.'),

      heading('4.2 Copagos — Circular 048/2025', HeadingLevel.HEADING_2),
      para('El sistema calcula automáticamente el copago basado en los parámetros ya cargados en la tabla copago_param:'),

      simpleTable(
        ['Condición', 'Copago'],
        [
          ['Triage I (Resucitación)', 'Exento'],
          ['Triage II (Emergencia)', 'Exento'],
          ['Triage III (Urgencia)', 'Exento'],
          ['Triage IV (Prioritaria) — Contributivo < 2 SMLMV', '11.5% hasta $373,715'],
          ['Triage IV (Prioritaria) — Contributivo 2-5 SMLMV', '17.3% hasta $1,497,644'],
          ['Triage IV (Prioritaria) — Contributivo > 5 SMLMV', '23.0% hasta $2,995,409'],
          ['Triage IV-V — Subsidiado', '10% hasta $651,155'],
          ['Menores de 18 años', 'Exento siempre'],
          ['Embarazadas (parto/postparto)', 'Exento siempre'],
          ['Alto costo (cáncer, VIH, raras)', 'Exento siempre'],
          ['Adultos >60 en Sisbén nivel 1', 'Exento siempre'],
          ['Víctimas conflicto armado', 'Exento siempre'],
        ],
        [5000, 4360]
      ),

      divider(),

      // ══════════════════════════════════════════════════════════════════════
      // 5. MODELO DE DATOS
      // ══════════════════════════════════════════════════════════════════════
      heading('5. Modelo de Datos'),
      para('El módulo extiende las tablas existentes y agrega nuevas para soportar la funcionalidad completa:'),

      heading('5.1 Extensiones a tabla admisiones', HeadingLevel.HEADING_2),
      para('Nuevas columnas para soportar el flujo de admisiones urgencias:'),

      simpleTable(
        ['Columna', 'Tipo', 'Descripción'],
        [
          ['pagador_tipo', 'TEXT', 'eps_contributivo | eps_subsidiado | arl | soat | poliza | particular'],
          ['pagador_nombre', 'TEXT', 'Nombre del pagador (ej: "Sanitas", "Sura ARL")'],
          ['pagador_codigo', 'TEXT', 'Código del pagador en ADRES/Supersalud'],
          ['regimen', 'TEXT', 'contributivo | subsidiado | especial | excepcion'],
          ['afiliacion_estado', 'TEXT', 'activo | inactivo | pendiente'],
          ['afiliacion_validada_en', 'TIMESTAMP', 'Momento de la validación de derechos'],
          ['afiliacion_validada_por', 'TEXT', 'Usuario que validó'],
          ['numero_autorizacion', 'TEXT', 'Número de autorización del pagador (ya existe)'],
          ['causa_externa', 'TEXT', 'Código RIPS causa externa (accidente, enfermedad, etc.)'],
          ['dx_ingreso', 'TEXT', 'CIE-10 diagnóstico principal ingreso'],
          ['dx_ingreso_rel1', 'TEXT', 'CIE-10 diagnóstico relacionado ingreso 1'],
          ['dx_ingreso_rel2', 'TEXT', 'CIE-10 diagnóstico relacionado ingreso 2'],
          ['dx_ingreso_rel3', 'TEXT', 'CIE-10 diagnóstico relacionado ingreso 3'],
          ['dx_egreso', 'TEXT', 'CIE-10 diagnóstico principal egreso'],
          ['condicion_salida', 'TEXT', 'Código condición salida urgencia (RIPS)'],
          ['copago_calculado', 'REAL', 'Copago calculado automáticamente'],
          ['copago_exento', 'INTEGER', '1 si está exento de copago'],
          ['copago_motivo_exencion', 'TEXT', 'Motivo de exención (triage I-III, menor, embarazo, etc.)'],
          ['acompanante_nombre', 'TEXT', 'Nombre del acompañante'],
          ['acompanante_parentesco', 'TEXT', 'Parentesco del acompañante'],
          ['acompanante_celular', 'TEXT', 'Celular del acompañante'],
          ['admision_operador', 'TEXT', 'Usuario que realizó la admisión'],
          ['admision_puesto', 'TEXT', 'Puesto desde donde se realizó la admisión'],
        ],
        [3000, 1500, 4860]
      ),

      new Paragraph({ spacing: { after: 200 }, children: [] }),

      heading('5.2 Nueva tabla: admision_timeline', HeadingLevel.HEADING_2),
      para('Registro granular de cada evento en la vida de una admisión. Complementa audit_trail con datos estructurados específicos del flujo clínico:'),

      simpleTable(
        ['Columna', 'Tipo', 'Descripción'],
        [
          ['id', 'SERIAL PK', 'ID autoincrementable'],
          ['admision_id', 'INTEGER FK', 'Referencia a admisiones.id'],
          ['evento', 'TEXT', 'anuncio | triage_llamado | triage_asignado | admision_llamado | admision_completa | consulta_llamado | atendido | egreso'],
          ['detalle', 'TEXT', 'Descripción legible del evento'],
          ['datos', 'TEXT (JSON)', 'Datos estructurados del evento (triage nivel, pagador, dx, etc.)'],
          ['usuario', 'TEXT', 'Usuario que generó el evento'],
          ['puesto', 'TEXT', 'Puesto de atención desde donde se generó'],
          ['ts', 'TIMESTAMP', 'Momento exacto del evento'],
          ['ip', 'TEXT', 'IP del cliente'],
        ],
        [2000, 1500, 5860]
      ),

      new Paragraph({ spacing: { after: 200 }, children: [] }),

      heading('5.3 Nueva tabla: historia_clinica_urgencias', HeadingLevel.HEADING_2),
      para('Registro clínico de la atención de urgencias. Se crea una entrada por cada admisión de urgencias:'),

      simpleTable(
        ['Columna', 'Tipo', 'Descripción'],
        [
          ['id', 'SERIAL PK', 'ID autoincrementable'],
          ['admision_id', 'INTEGER FK', 'Referencia a admisiones.id'],
          ['paciente_id', 'INTEGER FK', 'Referencia a pacientes.id'],
          ['motivo_consulta', 'TEXT', 'Motivo de consulta descrito por el paciente'],
          ['enfermedad_actual', 'TEXT', 'Descripción de la enfermedad actual'],
          ['antecedentes', 'TEXT (JSON)', 'Antecedentes relevantes (patológicos, quirúrgicos, alérgicos, farmacológicos)'],
          ['signos_vitales', 'TEXT (JSON)', '{fc, fr, ta_sistolica, ta_diastolica, temperatura, spo2, peso, talla, glasgow}'],
          ['examen_fisico', 'TEXT', 'Examen físico por sistemas'],
          ['dx_principal', 'TEXT', 'CIE-10 diagnóstico principal'],
          ['dx_relacionados', 'TEXT (JSON)', 'Array de CIE-10 diagnósticos relacionados'],
          ['plan_manejo', 'TEXT', 'Plan de manejo / conducta'],
          ['ordenes_medicas', 'TEXT (JSON)', 'Órdenes: medicamentos, laboratorios, imágenes, procedimientos'],
          ['observaciones', 'TEXT', 'Observaciones adicionales'],
          ['medico_id', 'INTEGER FK', 'Médico tratante'],
          ['medico_nombre', 'TEXT', 'Nombre del médico (desnormalizado para reportes)'],
          ['estado', 'TEXT', 'abierta | en_curso | cerrada'],
          ['creado_en', 'TIMESTAMP', 'Fecha/hora de creación'],
          ['actualizado_en', 'TIMESTAMP', 'Última actualización'],
        ],
        [2200, 1500, 5660]
      ),

      divider(),

      // ══════════════════════════════════════════════════════════════════════
      // 6. API ENDPOINTS
      // ══════════════════════════════════════════════════════════════════════
      heading('6. API Endpoints — admisiones_engine.py'),
      para('Nuevo blueprint independiente que se registra en app.py:'),

      simpleTable(
        ['Método', 'Ruta', 'Descripción', 'Auth'],
        [
          ['GET', '/api/admisiones/paciente/<doc>', 'Buscar paciente + historial admisiones', 'Sí'],
          ['POST', '/api/admisiones/paciente', 'Crear/actualizar paciente (apertura HC)', 'Sí'],
          ['GET', '/api/admisiones/admision/<id>', 'Detalle completo de una admisión', 'Sí'],
          ['PUT', '/api/admisiones/admision/<id>', 'Actualizar datos de admisión (pagador, dx, copago)', 'Sí'],
          ['GET', '/api/admisiones/admision/<id>/timeline', 'Timeline minuto a minuto de una admisión', 'Sí'],
          ['POST', '/api/admisiones/validar-derechos', 'Registrar resultado de validación de derechos', 'Sí'],
          ['POST', '/api/admisiones/calcular-copago', 'Calcular copago automático', 'No (readonly)'],
          ['POST', '/api/admisiones/completar', 'Marcar admisión como completa', 'Sí'],
          ['GET', '/api/admisiones/dashboard', 'Métricas en tiempo real de urgencias', 'Sí'],
          ['GET', '/api/admisiones/buscar', 'Buscar admisiones con filtros', 'Sí'],
          ['GET', '/api/admisiones/historia/<id>', 'Historia clínica de urgencias', 'Sí'],
          ['POST', '/api/admisiones/historia', 'Crear/actualizar historia clínica urgencias', 'Sí'],
        ],
        [900, 3400, 3560, 1500]
      ),

      divider(),

      // ══════════════════════════════════════════════════════════════════════
      // 7. VISTA DE ADMISIONES — REDESIGN
      // ══════════════════════════════════════════════════════════════════════
      heading('7. Vista de Admisiones — Rediseño'),
      para('La vista actual de /atencion/admisiones se convierte en el punto de entrada. Cuando el operador llama a un paciente y lo tiene al frente, la pantalla se expande a un workspace completo con 4 pestañas:'),

      heading('7.1 Pestaña: Paciente', HeadingLevel.HEADING_2),
      bulletPara('Datos demográficos: tipo doc, número, nombres, apellidos, fecha nacimiento, género, celular, email, dirección, ciudad'),
      bulletPara('Si el paciente NO existe: formulario de creación (apertura de historia)'),
      bulletPara('Si el paciente YA existe: datos precargados, editables. Historial de admisiones anteriores'),
      bulletPara('Indicador visual: "Paciente nuevo" (verde) vs "Paciente conocido — 3 visitas anteriores" (azul)'),

      heading('7.2 Pestaña: Aseguramiento', HeadingLevel.HEADING_2),
      bulletPara('Selector de tipo de pagador: EPS Contributivo, EPS Subsidiado, ARL, SOAT, Póliza, Particular'),
      bulletPara('Para EPS: campo de búsqueda por cédula → botón "Consultar ADRES" → muestra resultado (EPS, régimen, estado)'),
      bulletPara('Para ARL: campos de empresa, NIT empleador, ARL, número reporte accidente'),
      bulletPara('Para SOAT: campos de placa vehículo, aseguradora, número de póliza, FURIPS'),
      bulletPara('Para Póliza: campos de aseguradora, número póliza, titular'),
      bulletPara('Para Particular: sin campos adicionales, tarifa directa'),
      bulletPara('Campo de número de autorización (para EPS/ARL que lo requieran)'),
      bulletPara('Cálculo automático de copago con motivo de exención si aplica'),

      heading('7.3 Pestaña: Clínico', HeadingLevel.HEADING_2),
      bulletPara('Resumen de triage: nivel asignado, notas de enfermería, signos vitales'),
      bulletPara('Causa externa (selector RIPS): enfermedad general, accidente de trabajo, accidente de tránsito, violencia, lesión autoinfligida, otro'),
      bulletPara('Diagnóstico de ingreso CIE-10 (autocompletado con buscador)'),
      bulletPara('Campo de observaciones clínicas'),

      heading('7.4 Pestaña: Timeline', HeadingLevel.HEADING_2),
      bulletPara('Vista cronológica vertical de cada evento desde que el paciente se anunció'),
      bulletPara('Cada evento muestra: hora, actor, acción, detalle'),
      bulletPara('Código de color por tipo: kiosco (gris), triage (naranja), admisiones (azul), consulta (verde)'),
      bulletPara('Auto-scroll a lo más reciente, actualización en tiempo real vía SSE'),

      divider(),

      // ══════════════════════════════════════════════════════════════════════
      // 8. DASHBOARD OPERATIVO DE URGENCIAS
      // ══════════════════════════════════════════════════════════════════════
      heading('8. Dashboard Operativo de Urgencias'),
      para('Panel en tiempo real que muestra el estado completo del servicio de urgencias. Accesible desde /admisiones/dashboard. Se actualiza con SSE cada cambio de estado:'),

      heading('8.1 KPIs en Tiempo Real (barra superior)', HeadingLevel.HEADING_2),

      simpleTable(
        ['Métrica', 'Cálculo', 'Visualización'],
        [
          ['Total hoy', 'COUNT admisiones WHERE DATE(creado_en) = hoy', 'Número grande + tendencia vs ayer'],
          ['En espera', 'COUNT WHERE estado IN (kiosco, triaje)', 'Número + barra de proporción'],
          ['En atención', 'COUNT WHERE estado IN (llamando, admision)', 'Número + barra'],
          ['Atendidos', 'COUNT WHERE estado = atendido', 'Número + barra'],
          ['Tiempo promedio espera', 'AVG(fecha_llamado - creado_en) para atendidos hoy', 'Minutos + semáforo (verde<15, amarillo<30, rojo>30)'],
          ['Tiempo promedio atención', 'AVG(fecha_salida - fecha_admision_inicio)', 'Minutos'],
        ],
        [2200, 3800, 3360]
      ),

      new Paragraph({ spacing: { after: 200 }, children: [] }),

      heading('8.2 Distribución por Etapa (centro)', HeadingLevel.HEADING_2),
      para('Visualización tipo embudo/pipeline que muestra cuántos pacientes hay en cada etapa del flujo:'),
      bulletPara('Kiosco (esperando triage): X pacientes — lista con turno y tiempo de espera'),
      bulletPara('Triage (clasificados, esperando admisiones): X pacientes — agrupados por nivel I-V'),
      bulletPara('Admisiones (siendo procesados): X pacientes — nombre del operador'),
      bulletPara('Consulta (esperando doctor): X pacientes — agrupados por prioridad'),
      bulletPara('Llamando (en proceso): X pacientes — destino actual'),
      bulletPara('Atendidos: X pacientes — hora de egreso'),

      heading('8.3 Distribución por Triage (lateral)', HeadingLevel.HEADING_2),
      para('Barras horizontales mostrando cuántos pacientes hay por nivel de triage, con colores:'),
      bulletPara('Nivel I — Resucitación (rojo): X', { color: RED }),
      bulletPara('Nivel II — Emergencia (naranja): X', { color: ORANGE }),
      bulletPara('Nivel III — Urgencia (ámbar): X', { color: AMBER }),
      bulletPara('Nivel IV — Prioritaria (verde): X', { color: GREEN }),
      bulletPara('Nivel V — No urgente (azul): X', { color: BLUE }),

      heading('8.4 Alertas Automáticas', HeadingLevel.HEADING_2),
      bulletPara('Paciente en espera > 30 minutos sin ser llamado → alerta amarilla'),
      bulletPara('Paciente Triage I-II esperando > 5 minutos → alerta roja'),
      bulletPara('Puesto de atención sin actividad > 20 minutos → alerta de inactividad'),
      bulletPara('Cola de espera > 15 pacientes → alerta de saturación'),

      divider(),

      // ══════════════════════════════════════════════════════════════════════
      // 9. RIPS — CAMPOS OBLIGATORIOS URGENCIAS
      // ══════════════════════════════════════════════════════════════════════
      heading('9. RIPS — Estructura JSON para Urgencias'),
      para('Según la Resolución 2275 de 2023, cada atención de urgencias debe generar un registro RIPS con la siguiente estructura. El sistema debe capturar estos datos durante el flujo para generar el JSON al momento de facturación:'),

      simpleTable(
        ['Campo RIPS', 'Código', 'Obligatorio', 'Fuente en Arthemis'],
        [
          ['codPrestador', 'R01', 'Sí', 'tenant_config.cod_habilitacion'],
          ['fechaInicioAtencion', 'R02', 'Sí', 'admisiones.creado_en (YYYY-MM-DD hh:mm)'],
          ['causaMotivoAtencion', 'R03', 'Sí', 'admisiones.causa_externa'],
          ['codDiagnosticoPrincipalIngreso', 'R04', 'Sí', 'admisiones.dx_ingreso'],
          ['codDiagnosticoRelacionadoIngreso1', 'R05', 'No', 'admisiones.dx_ingreso_rel1'],
          ['codDiagnosticoRelacionadoIngreso2', 'R06', 'No', 'admisiones.dx_ingreso_rel2'],
          ['codDiagnosticoRelacionadoIngreso3', 'R07', 'No', 'admisiones.dx_ingreso_rel3'],
          ['codDiagnosticoPrincipalEgreso', 'R08', 'Sí', 'historia_clinica_urgencias.dx_principal (al cierre)'],
          ['condicionSalidaUrgencia', 'R09', 'Sí', 'admisiones.condicion_salida'],
          ['codDiagnosticoRelacionadoEgreso', 'R10', 'No', 'historia_clinica_urgencias.dx_relacionados[0]'],
          ['fechaEgreso', 'R11', 'Sí', 'admisiones.fecha_salida (YYYY-MM-DD hh:mm)'],
          ['consecutivo', '—', 'Sí', 'Secuencial dentro del array de urgencias del usuario'],
        ],
        [3200, 900, 1200, 4060]
      ),

      divider(),

      // ══════════════════════════════════════════════════════════════════════
      // 10. INTEGRACIONES FUTURAS
      // ══════════════════════════════════════════════════════════════════════
      heading('10. Integraciones Futuras (Make / N8N)'),
      para('Las siguientes integraciones se conectarán vía Make o N8N para automatizar procesos que hoy son manuales:'),

      simpleTable(
        ['Integración', 'Prioridad', 'Descripción', 'Trigger'],
        [
          ['ADRES BDUA', 'Alta', 'Consulta automática de afiliación por cédula', 'Al ingresar documento del paciente'],
          ['EPS Autorizaciones', 'Alta', 'Solicitud/consulta de autorizaciones con EPS principales', 'Al completar validación de derechos'],
          ['RIPS JSON', 'Alta', 'Generación automática del JSON RIPS para facturación', 'Al marcar egreso del paciente'],
          ['ARL Reporte', 'Media', 'Reporte de accidente de trabajo a ARL', 'Cuando pagador_tipo = arl'],
          ['SOAT/FURIPS', 'Media', 'Generación del FURIPS para accidentes de tránsito', 'Cuando pagador_tipo = soat'],
          ['WhatsApp Notif', 'Media', 'Notificación al paciente/acompañante de estado', 'Cambios de estado en el flujo'],
          ['HCE Interoperable', 'Baja (2027)', 'RDA según Res. 1888/2025, HL7 FHIR', 'Al cierre de historia clínica'],
        ],
        [1800, 1000, 3960, 2600]
      ),

      divider(),

      // ══════════════════════════════════════════════════════════════════════
      // 11. PLAN DE IMPLEMENTACIÓN
      // ══════════════════════════════════════════════════════════════════════
      heading('11. Plan de Implementación'),

      heading('Fase 1 — Demo Funcional (prioridad inmediata)', HeadingLevel.HEADING_2),
      para('Objetivo: módulo de admisiones urgencias 100% funcional para demo. Todo real, todo pulido.'),
      bulletPara('Tablas: extensiones a admisiones + admision_timeline + historia_clinica_urgencias'),
      bulletPara('Backend: admisiones_engine.py con todos los endpoints'),
      bulletPara('Vista admisiones: workspace completo con 4 pestañas (Paciente, Aseguramiento, Clínico, Timeline)'),
      bulletPara('Validación de derechos: manual (operador ingresa datos de ADRES)'),
      bulletPara('Copago: cálculo automático con tabla copago_param existente'),
      bulletPara('Dashboard: panel con KPIs, distribución por etapa y triage, alertas'),
      bulletPara('Timeline: registro automático de cada evento del flujo'),
      bulletPara('Trazabilidad: quién abrió, qué agregó/quitó, historial completo'),
      bulletPara('RIPS: campos capturados durante el flujo, listos para generación JSON'),

      heading('Fase 2 — Automatizaciones', HeadingLevel.HEADING_2),
      bulletPara('Make/N8N: integración ADRES BDUA automática'),
      bulletPara('Make/N8N: solicitud de autorizaciones EPS'),
      bulletPara('Make/N8N: generación JSON RIPS al egreso'),
      bulletPara('Formularios específicos: reporte ARL, FURIPS SOAT'),
      bulletPara('Notificaciones WhatsApp al paciente/acompañante'),

      heading('Fase 3 — Interoperabilidad', HeadingLevel.HEADING_2),
      bulletPara('HCE Interoperable: RDA con HL7 FHIR según Resolución 1888/2025'),
      bulletPara('Integración con módulo de Historia Clínica general'),
      bulletPara('Integración con módulo de Facturación'),
      bulletPara('Reportes y estadísticas avanzadas'),

      divider(),

      // ══════════════════════════════════════════════════════════════════════
      // 12. NORMATIVA APLICABLE
      // ══════════════════════════════════════════════════════════════════════
      heading('12. Normativa Aplicable'),

      simpleTable(
        ['Norma', 'Tema', 'Impacto en Arthemis'],
        [
          ['Ley 100/1993 Art. 168', 'Atención obligatoria de urgencias', 'Nunca condicionar atención a validación de derechos'],
          ['Ley 1751/2015', 'Salud como derecho fundamental', 'Garantizar acceso sin barreras administrativas'],
          ['Resolución 5596/2015', 'Triage en urgencias (niveles I-V)', 'Sistema de triage implementado en módulo Atención'],
          ['Resolución 3100/2019', 'Habilitación servicios de salud', 'Requisitos mínimos de HC y registros'],
          ['Ley 1581/2012', 'Protección datos personales', 'Habeas data en cada admisión, consentimiento informado'],
          ['Resolución 2275/2023', 'RIPS JSON', 'Campos obligatorios de urgencias capturados en el flujo'],
          ['Circular 048/2025', 'Copagos y cuotas moderadoras', 'Tabla de parámetros para cálculo automático'],
          ['Resolución 1888/2025', 'HCE Interoperable (RDA, HL7 FHIR)', 'Futuro: generación de RDA al cierre de HC'],
          ['Sentencia T-210/2018', 'Acceso urgencias sin barreras', 'No negar atención por falta de documentos/afiliación'],
        ],
        [2400, 2800, 4160]
      ),

      new Paragraph({ spacing: { after: 400 }, children: [] }),
      para('— Fin del documento —', { align: AlignmentType.CENTER, italics: true, color: '999999' }),
    ],
  }],
});

// ── Generate ─────────────────────────────────────────────────────────────────
Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync('/root/ArthemisCOAL/docs/Admisiones_Urgencias_Diseño_v1.docx', buf);
  console.log('✅ Document generated');
});
