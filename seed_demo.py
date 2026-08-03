"""
seed_demo.py — Seed 15 realistic demo clinical histories for Arthemis Health COAL demo.
Run: python3 seed_demo.py
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
import core

conn, db = core.get_db()
cur = conn.cursor()
A = lambda q: core.adapt(q, db)

# ── 12 new patients (diverse Colombian names, docs, EPS) ──
PACIENTES = [
    ("CC","80123456","Pedro Antonio","Ramírez Castillo","1985-03-14","M","3105551234","Compensar"),
    ("CC","52987654","Luz Marina","Gómez Ospina","1972-11-28","F","3209876543","Sanitas"),
    ("CC","1098765432","Sebastián","Herrera Montoya","1998-07-02","M","3001234567","Sura"),
    ("TI","1234509876","Valentina","Ríos Castaño","2010-05-19","F","3187654321","Nueva EPS"),
    ("CC","79654321","Jorge Eliecer","Muñoz Patiño","1960-01-30","M","3112345678","Salud Total"),
    ("CE","E456789","María Alejandra","dos Santos Lima","1990-09-15","F","3156789012","Coomeva"),
    ("CC","1023987654","Camila Andrea","Vargas Restrepo","1995-12-08","F","3204567890","Famisanar"),
    ("CC","7812345","Roberto Carlos","Díaz Arango","1955-06-22","M","3178901234","Cafesalud"),
    ("CC","1087654321","Daniela","Moreno Suárez","2000-02-14","F","3143210987","EPS Sura"),
    ("CC","98765432","Héctor Fabio","Londoño Mejía","1968-08-05","M","3167890123","Coomeva"),
    ("RC","1122334455","Santiago","Pérez Ochoa","2023-04-10","M","3198765432","Nueva EPS"),
    ("CC","51234567","Gloria Patricia","Martínez Henao","1978-10-31","F","3134567890","Compensar"),
]

paciente_ids = []
for td, nd, nom, ape, fn, gen, cel, eps in PACIENTES:
    cur.execute(A("INSERT INTO pacientes(tipo_doc,num_doc,nombres,apellidos,fecha_nacimiento,genero,celular,eps)VALUES(?,?,?,?,?,?,?,?)"),
                (td, nd, nom, ape, fn, gen, cel, eps))
    conn.commit()
    p = core.row(cur, A("SELECT id FROM pacientes WHERE num_doc=?"), (nd,))
    paciente_ids.append(p['id'])
    print(f"  Paciente #{p['id']}: {nom} {ape}")

# ── 15 clinical histories with rich, varied data ──
HISTORIAS = [
    # (pac_idx, tipo_hc, motivo, causa, cie10_in, cie10_eg, condicion_eg, destino_eg, estado,
    #  turno, triage_nivel, fecha_base, medico, evoluciones[], ordenes[], prescripciones[], interconsultas[])
    (0, "urgencias", "Dolor torácico opresivo irradiado a brazo izquierdo de 2 horas de evolución, asociado a diaforesis y náuseas",
     "urgencia", "I20.0", "I20.9", "mejorado", "domicilio", "cerrada",
     "U-001", "II", "2026-07-15 08:30:00", "Dra. Carolina Mejía",
     [("Paciente masculino de 41 años con dolor torácico típico. TA 150/90, FC 98, SatO2 96%. ECG: supradesnivel ST en V1-V4. Troponinas elevadas.",
       "Síndrome coronario agudo con elevación ST anterior. Se inicia protocolo ACS.", "I20.0",
       '{"ta":"150/90","fc":98,"sat":96,"temp":36.5}')],
     [("laboratorio","903841","Troponina I ultrasensible",1,"stat","Dolor torácico agudo","I20.0"),
      ("imagenologia","879101","Radiografía de tórax PA y lateral",1,"urgente","Descartar cardiomegalia","I20.0"),
      ("laboratorio","903859","Hemograma completo",1,"urgente","Evaluación preoperatoria","I20.0")],
     [("Ácido Acetilsalicílico","19900123","300 mg","Tableta","Oral","300 mg","Dosis única","1 día","1","Masticar inmediatamente","I20.0"),
      ("Clopidogrel","20100456","75 mg","Tableta","Oral","300 mg carga","Dosis única","1 día","4","Dosis de carga","I20.0"),
      ("Enoxaparina","20050789","60 mg/0.6mL","Solución inyectable","Subcutánea","60 mg","Cada 12 horas","5 días","10","Anticoagulación","I20.0")],
     [("cardiologia","Cardiología","Síndrome coronario agudo STEMI anterior. Requiere evaluación para cateterismo cardíaco urgente","I20.0","stat")]),

    (1, "urgencias", "Caída de su propia altura con trauma en cadera derecha. Imposibilidad para la marcha desde hace 3 horas",
     "urgencia", "S72.0", "S72.00", "mejorado", "hospitalizacion", "cerrada",
     "U-002", "III", "2026-07-16 14:20:00", "Dr. Andrés Felipe Ruiz",
     [("Paciente femenina de 53 años con trauma en cadera derecha. Rotación externa y acortamiento de MID. EVA 8/10. TA 130/80, FC 88.",
       "Fractura de cuello femoral derecho. Se solicita Rx y valoración por ortopedia.", "S72.0",
       '{"ta":"130/80","fc":88,"sat":98,"temp":36.8}')],
     [("imagenologia","871021","Radiografía de pelvis AP",1,"urgente","Fractura cadera derecha","S72.0"),
      ("imagenologia","871022","Radiografía de cadera derecha",1,"urgente","Fractura cadera derecha","S72.0"),
      ("laboratorio","903859","Hemograma completo",1,"urgente","Pre-quirúrgico","S72.0"),
      ("laboratorio","903895","Creatinina sérica",1,"urgente","Pre-quirúrgico","S72.0")],
     [("Tramadol","19950111","50 mg/mL","Solución inyectable","Intravenosa","50 mg","Cada 8 horas","3 días","9","Analgesia","S72.0"),
      ("Dipirona","19880222","1g/2mL","Solución inyectable","Intravenosa","2g","Cada 6 horas","3 días","12","Analgesia alterna","S72.0")],
     [("ortopedia","Ortopedia y Traumatología","Fractura cuello femoral Garden III. Valorar para osteosíntesis vs artroplastia","S72.0","urgente")]),

    (2, "urgencias", "Crisis asmática severa con disnea progresiva de 6 horas de evolución, sin mejoría con inhalador de rescate",
     "enfermedad_general", "J45.1", "J45.1", "mejorado", "domicilio", "cerrada",
     "U-003", "II", "2026-07-18 22:15:00", "Dra. Sandra Milena Ortiz",
     [("Paciente masculino de 28 años asmático conocido. Sibilancias difusas, uso de músculos accesorios. FR 28, SatO2 89%, FC 110. PEF 40% del predicho.",
       "Exacerbación severa de asma bronquial. Nebulizaciones seriadas + corticoide sistémico. Monitoreo continuo.", "J45.1",
       '{"ta":"120/70","fc":110,"fr":28,"sat":89,"temp":36.2}'),
      ("Post-nebulización x3: Mejoría parcial. FR 22, SatO2 94%. Sibilancias leves. PEF 65%. Se continúa oxígeno y observación.",
       "Respuesta parcial al tratamiento. Continuar manejo y revalorar en 2 horas.", "J45.1",
       '{"ta":"118/72","fc":92,"fr":22,"sat":94,"temp":36.3}')],
     [("laboratorio","903854","Gases arteriales",1,"stat","Crisis asmática severa","J45.1")],
     [("Salbutamol","20000333","5 mg/mL","Solución para nebulización","Inhalatoria","0.5 mL + 3mL SSN","Cada 20 min x 3","1 día","3","Nebulización seriada","J45.1"),
      ("Prednisolona","19970444","50 mg","Tableta","Oral","50 mg","Dosis única","1 día","1","Corticoide sistémico","J45.1"),
      ("Bromuro de Ipratropio","20080555","0.25 mg/mL","Solución para nebulización","Inhalatoria","1 mL","Cada 20 min x 3","1 día","3","Combinada con salbutamol","J45.1")],
     []),

    (3, "urgencias", "Paciente pediátrica de 16 años con fiebre alta de 39.5°C de 3 días de evolución, cefalea intensa y mialgias generalizadas",
     "urgencia", "A90", "A90", "mejorado", "domicilio", "cerrada",
     "U-004", "III", "2026-07-20 10:45:00", "Dr. Mauricio Velásquez",
     [("Adolescente femenina con síndrome febril agudo. Petequias en extremidades. Torniquete positivo. TA 100/60, FC 104, Temp 39.2°C. Plaquetas 95,000.",
       "Dengue con signos de alarma. Hidratación IV, monitoreo de hematocrito y plaquetas cada 6 horas.", "A90",
       '{"ta":"100/60","fc":104,"temp":39.2,"sat":97}')],
     [("laboratorio","903859","Hemograma completo",1,"stat","Dengue - plaquetas","A90"),
      ("laboratorio","903856","NS1 Dengue + IgM/IgG",1,"stat","Confirmación dengue","A90"),
      ("laboratorio","903861","Función hepática completa",1,"urgente","Dengue signos alarma","A90")],
     [("Acetaminofén","19850666","500 mg","Tableta","Oral","500 mg","Cada 6 horas","5 días","20","Antipirético. NO usar AINEs","A90")],
     []),

    (4, "urgencias", "Dolor abdominal en fosa ilíaca derecha de 12 horas de evolución, progresivo, con náuseas y vómito",
     "urgencia", "K35.9", "K35.80", "mejorado", "hospitalizacion", "cerrada",
     "U-005", "III", "2026-07-22 06:00:00", "Dra. Carolina Mejía",
     [("Paciente masculino de 65 años con dolor en FID, defensa muscular localizada. McBurney positivo, Blumberg positivo. Temp 38.1°C. Leucocitos 15,800.",
       "Apendicitis aguda. Se solicita TAC abdominal y valoración por cirugía general. NPO.", "K35.9",
       '{"ta":"140/85","fc":94,"temp":38.1,"sat":97}')],
     [("imagenologia","879301","TAC de abdomen con contraste",1,"urgente","Sospecha apendicitis","K35.9"),
      ("laboratorio","903859","Hemograma completo",1,"stat","Leucocitosis","K35.9"),
      ("laboratorio","903866","PCR cuantitativa",1,"urgente","Marcador inflamatorio","K35.9"),
      ("laboratorio","903896","Parcial de orina",1,"urgente","Descartar ITU","K35.9")],
     [("Dipirona","19880222","1g/2mL","Solución inyectable","Intravenosa","2g","Cada 6 horas","1 día","4","Analgesia","K35.9"),
      ("Metoclopramida","19900777","10 mg/2mL","Solución inyectable","Intravenosa","10 mg","Cada 8 horas","1 día","3","Antiemético","K35.9")],
     [("cirugia_general","Cirugía General","Cuadro clínico compatible con apendicitis aguda. Solicito valoración para apendicectomía.","K35.9","urgente")]),

    (5, "consulta", "Control prenatal semana 28. Gestante sin complicaciones aparentes, primer embarazo",
     "control", "Z34.0", "Z34.0", "estable", "domicilio", "cerrada",
     "C-001", None, "2026-07-23 09:00:00", "Dra. Patricia Gómez",
     [("Gestante de 28 semanas por FUR. Sin complicaciones. AU 27cm, FCF 144 lpm. Movimientos fetales presentes. TA 110/70. IMC pregestacional normal.",
       "Embarazo de 28 semanas curso normal. Solicitar laboratorios de III trimestre. Próximo control en 2 semanas.", "Z34.0",
       '{"ta":"110/70","fc":78,"temp":36.4}')],
     [("laboratorio","903859","Hemograma completo",1,"normal","Control prenatal III trimestre","Z34.0"),
      ("laboratorio","903869","Glucosa en ayunas",1,"normal","Tamizaje diabetes gestacional","Z34.0"),
      ("imagenologia","881302","Ecografía obstétrica",1,"normal","Biometría fetal semana 28","Z34.0")],
     [("Sulfato Ferroso","19920888","200 mg","Tableta","Oral","200 mg","Cada 24 horas","30 días","30","Suplementación hierro","Z34.0"),
      ("Ácido Fólico","19930999","1 mg","Tableta","Oral","1 mg","Cada 24 horas","30 días","30","Suplementación","Z34.0"),
      ("Carbonato de Calcio","19941010","600 mg","Tableta","Oral","600 mg","Cada 12 horas","30 días","60","Suplementación calcio","Z34.0")],
     []),

    (6, "urgencias", "Laceración profunda en antebrazo derecho por accidente con vidrio. Sangrado activo controlado con presión",
     "urgencia", "S51.0", "S51.0", "mejorado", "domicilio", "cerrada",
     "U-006", "IV", "2026-07-24 16:30:00", "Dr. Andrés Felipe Ruiz",
     [("Paciente femenina de 31 años con laceración de 8cm en cara anterior de antebrazo derecho. No compromiso tendinoso ni vascular. Pulsos distales presentes.",
       "Herida cortante antebrazo derecho. Sutura con Nylon 4-0 (12 puntos). Profilaxis antitetánica.", "S51.0",
       '{"ta":"115/70","fc":82,"sat":99,"temp":36.5}')],
     [],
     [("Cefalexina","19960111","500 mg","Cápsula","Oral","500 mg","Cada 6 horas","7 días","28","Antibiótico profiláctico","S51.0"),
      ("Ibuprofeno","19870222","400 mg","Tableta","Oral","400 mg","Cada 8 horas","5 días","15","Antiinflamatorio/analgésico","S51.0")],
     []),

    (7, "urgencias", "Disnea súbita con dolor pleurítico en hemitórax derecho. Antecedente de cirugía de rodilla hace 10 días",
     "urgencia", "I26.9", "I26.0", "mejorado", "hospitalizacion", "cerrada",
     "U-007", "I", "2026-07-25 03:20:00", "Dra. Sandra Milena Ortiz",
     [("Paciente masculino de 67 años post-operatorio de artroplastia de rodilla. Disnea severa, dolor pleurítico. TA 90/60, FC 120, SatO2 85%, FR 30. Dímero D >5000.",
       "Alta sospecha de tromboembolismo pulmonar masivo. Angiotac urgente. Soporte hemodinámico. Considerar trombolisis.", "I26.9",
       '{"ta":"90/60","fc":120,"fr":30,"sat":85,"temp":36.0}'),
      ("Post-angiotac: TEP bilateral confirmado con compromiso de tronco principal derecho. Se inicia heparina no fraccionada en infusión. Traslado a UCI.",
       "TEP masivo confirmado. Anticoagulación plena con HNF. UCI para monitoreo hemodinámico invasivo.", "I26.0",
       '{"ta":"95/62","fc":112,"fr":26,"sat":90,"temp":36.1}')],
     [("imagenologia","879401","Angiotac de tórax",1,"stat","Sospecha TEP masivo","I26.9"),
      ("laboratorio","903870","Dímero D cuantitativo",1,"stat","TEP","I26.9"),
      ("laboratorio","903854","Gases arteriales",1,"stat","Hipoxemia severa","I26.9"),
      ("laboratorio","903871","TP, TPT, INR",1,"stat","Pre-anticoagulación","I26.9")],
     [("Heparina sódica","19840333","25000 UI/5mL","Solución inyectable","Intravenosa","Bolo 80 UI/kg + infusión 18 UI/kg/h","Infusión continua","5 días","5","Anticoagulación plena","I26.0")],
     [("neumologia","Neumología","TEP masivo bilateral. Paciente en UCI. Valoración para decisión de trombolisis sistémica vs intervención.","I26.0","stat"),
      ("medicina_intensiva","Medicina Intensiva / UCI","TEP masivo con compromiso hemodinámico. Requiere monitoreo invasivo y soporte vasopresor.","I26.0","stat")]),

    (8, "consulta", "Cefalea crónica recurrente tipo migraña de 6 meses de evolución, 3-4 episodios por semana",
     "consulta", "G43.9", "G43.0", "estable", "domicilio", "cerrada",
     "C-002", None, "2026-07-26 11:00:00", "Dr. Mauricio Velásquez",
     [("Paciente femenina de 26 años con migraña crónica. Cefalea pulsátil hemicraneal, fotofobia, fonofobia. Examen neurológico normal. Escala MIDAS grado III.",
       "Migraña sin aura crónica. Inicio profilaxis con propranolol. Educación en identificación de triggers. Control en 4 semanas.", "G43.0",
       '{"ta":"105/65","fc":72,"temp":36.3}')],
     [],
     [("Propranolol","19860444","40 mg","Tableta","Oral","40 mg","Cada 12 horas","30 días","60","Profilaxis migraña","G43.0"),
      ("Sumatriptán","20020555","50 mg","Tableta","Oral","50 mg","SOS al inicio de crisis","30 días","4","Rescate migraña","G43.0")],
     []),

    (9, "urgencias", "Paciente traído por familiares con alteración del estado de conciencia y glucometría de 42 mg/dL",
     "urgencia", "E16.2", "E11.6", "mejorado", "domicilio", "cerrada",
     "U-008", "I", "2026-07-27 19:45:00", "Dra. Carolina Mejía",
     [("Paciente masculino de 58 años diabético tipo 2 en manejo con glibenclamida e insulina. Glasgow 10 (O3V3M4). Glucometría 42 mg/dL. Diaforesis profusa.",
       "Hipoglucemia severa secundaria a sobredosis de glibenclamida. Bolo de dextrosa al 50%. Monitoreo glucométrico horario.", "E16.2",
       '{"ta":"160/95","fc":108,"temp":36.0,"glasgow":10}'),
      ("Post-rescate: Glucometría 185 mg/dL. Glasgow 15. Paciente alerta y orientado. Se ajusta esquema hipoglucemiante. Suspender glibenclamida.",
       "Recuperación completa de hipoglucemia. Ajuste terapéutico: suspender glibenclamida, continuar metformina, ajustar insulina.", "E11.6",
       '{"ta":"135/82","fc":82,"temp":36.4,"glasgow":15}')],
     [("laboratorio","903869","Glucosa sérica",1,"stat","Hipoglucemia severa","E16.2"),
      ("laboratorio","903862","HbA1c",1,"urgente","Control metabólico","E11.6"),
      ("laboratorio","903895","Creatinina + BUN",1,"urgente","Función renal","E11.6")],
     [("Metformina","19980666","850 mg","Tableta","Oral","850 mg","Cada 12 horas","30 días","60","Continuar","E11.6"),
      ("Insulina Glargina","20100777","100 UI/mL","Solución inyectable","Subcutánea","14 UI","Cada 24 horas (noche)","30 días","1","Ajuste dosis (prev 20 UI)","E11.6")],
     [("endocrinologia","Endocrinología","Hipoglucemia severa en DM2. Múltiples episodios. Requiere ajuste integral de esquema.","E11.6","preferente")]),

    (10, "urgencias", "Lactante de 3 años con dificultad respiratoria, rinorrea, tos seca y fiebre de 38.5°C desde ayer",
     "urgencia", "J21.0", "J21.0", "mejorado", "domicilio", "cerrada",
     "U-009", "III", "2026-07-28 07:30:00", "Dr. Mauricio Velásquez",
     [("Lactante masculino de 3 años con cuadro respiratorio de 48 horas. Tirajes subcostales leves. Sibilancias espiratorias. SatO2 93%. Temp 38.2°C.",
       "Bronquiolitis aguda por VSR probable. Oxígeno por cánula, nebulización con SSN hipertónica. Hidratación oral vigilada.", "J21.0",
       '{"fc":130,"fr":42,"sat":93,"temp":38.2}')],
     [("laboratorio","903856","Panel viral respiratorio",1,"urgente","Identificar agente etiológico","J21.0")],
     [("Acetaminofén","19850666","150 mg/5mL","Jarabe","Oral","7.5 mL (225 mg)","Cada 6 horas","3 días","60 mL","Antipirético. Dosis: 15mg/kg","J21.0")],
     []),

    (11, "consulta", "Dolor lumbar crónico de 8 meses de evolución con irradiación a miembro inferior derecho",
     "enfermedad_general", "M54.5", "M51.1", "estable", "domicilio", "cerrada",
     "C-003", None, "2026-07-29 15:00:00", "Dr. Andrés Felipe Ruiz",
     [("Paciente femenina de 48 años con lumbalgia crónica y ciatalgia derecha. Lasègue positivo 30°. Fuerza 4/5 en dorsiflexión pie derecho. ROT aquíleos disminuidos.",
       "Radiculopatía L5-S1 derecha. Solicitar RMN lumbosacra. Manejo con pregabalina y terapia física.", "M51.1",
       '{"ta":"120/78","fc":74,"temp":36.5}')],
     [("imagenologia","883101","RMN de columna lumbosacra",1,"preferente","Radiculopatía L5-S1","M51.1")],
     [("Pregabalina","20060888","75 mg","Cápsula","Oral","75 mg","Cada 12 horas","30 días","60","Dolor neuropático","M51.1"),
      ("Naproxeno","19870999","250 mg","Tableta","Oral","250 mg","Cada 8 horas","10 días","30","AINE","M54.5")],
     [("rehabilitacion","Medicina Física y Rehabilitación","Radiculopatía L5-S1. Solicito programa de terapia física 10 sesiones + TENS","M51.1","normal")]),

    (6, "urgencias", "Accidente de tránsito — motociclista. Trauma craneoencefálico moderado con pérdida de consciencia",
     "accidente_transito", "S06.0", "S06.0", "mejorado", "hospitalizacion", "abierta",
     "U-010", "I", "2026-08-01 23:10:00", "Dra. Sandra Milena Ortiz",
     [("Paciente femenina de 31 años — accidente de moto. Glasgow 12 (O3V4M5). Herida en región parietal izquierda. Anisocoria leve. TA 100/65, FC 105.",
       "TEC moderado. TAC cráneo urgente. Neuroprotección. Inmovilización cervical hasta descartar lesión.", "S06.0",
       '{"ta":"100/65","fc":105,"sat":95,"temp":36.2,"glasgow":12}')],
     [("imagenologia","879501","TAC cráneo simple",1,"stat","TEC moderado","S06.0"),
      ("imagenologia","879502","Rx columna cervical AP/lateral",1,"stat","Descartar fractura cervical","S06.0"),
      ("laboratorio","903859","Hemograma completo",1,"stat","Trauma","S06.0"),
      ("laboratorio","903871","TP, TPT, INR",1,"stat","Coagulación pre-quirúrgica","S06.0")],
     [("Fenitoína","19840111","250 mg/5mL","Solución inyectable","Intravenosa","15 mg/kg dosis carga","Dosis única","1 día","1","Profilaxis convulsiones","S06.0")],
     [("neurocirugia","Neurocirugía","TEC moderado Glasgow 12 con anisocoria. TAC cráneo pendiente. Valoración urgente para decisión quirúrgica.","S06.0","stat"),
      ("ortopedia","Ortopedia y Traumatología","Politrauma — descartar fracturas asociadas en extremidades y pelvis","S06.0","urgente")]),

    (1, "consulta", "Control post-operatorio fractura cadera derecha — 6 semanas post osteosíntesis",
     "control", "Z09.8", "Z09.8", "estable", "domicilio", "cerrada",
     "C-004", None, "2026-08-02 10:30:00", "Dr. Andrés Felipe Ruiz",
     [("Paciente femenina de 53 años, 6 semanas post-osteosíntesis cadera derecha. Herida quirúrgica sana. Marcha con andador. Arcos de movilidad en recuperación.",
       "Evolución satisfactoria post-osteosíntesis. Continuar terapia física. Control con Rx en 6 semanas. Retiro de material en 12-18 meses.", "Z09.8",
       '{"ta":"125/75","fc":72,"temp":36.4}')],
     [("imagenologia","871022","Rx cadera derecha control",1,"normal","Control post-quirúrgico","Z09.8")],
     [],
     []),

    (9, "urgencias", "Dolor precordial atípico con irradiación a epigastrio. Antecedente de infarto previo hace 2 años",
     "urgencia", "I20.9", "K21.0", "mejorado", "domicilio", "cerrada",
     "U-011", "II", "2026-08-03 05:15:00", "Dra. Carolina Mejía",
     [("Paciente masculino de 58 años con antecedente de IAM. Dolor retroesternal/epigástrico urente. ECG sin cambios agudos. Troponinas negativas x2.",
       "Dolor torácico no cardíaco — probable ERGE. Troponinas negativas seriadas, ECG normal. Iniciar IBP. Control ambulatorio con gastroenterología.", "K21.0",
       '{"ta":"145/88","fc":84,"sat":98,"temp":36.5}')],
     [("laboratorio","903841","Troponina I ultrasensible (seriada x2)",2,"stat","Descartar SCA","I20.9"),
      ("laboratorio","903859","Hemograma completo",1,"urgente","Evaluación general","I20.9")],
     [("Omeprazol","19900333","20 mg","Cápsula","Oral","20 mg","Cada 12 horas","14 días","28","IBP para ERGE","K21.0")],
     []),
]

print(f"\nCreando {len(HISTORIAS)} historias clínicas...")

for idx, h in enumerate(HISTORIAS):
    (pac_i, tipo_hc, motivo, causa, cie10_in, cie10_eg, cond_eg, dest_eg, estado,
     turno, triage_n, fecha, medico, evoluciones, ordenes, rxs, ics) = h

    pid = paciente_ids[pac_i]
    id_adm = f"DEMO-{turno}-{pid}"
    nombre_p = f"{PACIENTES[pac_i][2]} {PACIENTES[pac_i][3]}"

    # Create admission
    cur.execute(A(
        "INSERT INTO admisiones(id_adm,turno,turno_tipo,estado,nombre_temp,doc_num_temp,"
        "servicio_nombre,paciente_id,triage_nivel,creado_en,hc_abierta)"
        "VALUES(?,?,?,?,?,?,?,?,?,?,1)"),
        (id_adm, turno, 'general',
         'atendido' if estado == 'cerrada' else 'en_atencion',
         nombre_p, PACIENTES[pac_i][1],
         'Urgencias' if tipo_hc == 'urgencias' else 'Consulta externa',
         pid, triage_n, fecha))
    conn.commit()
    adm = core.row(cur, A("SELECT id FROM admisiones WHERE id_adm=?"), (id_adm,))
    adm_id = adm['id']

    # Create HC
    cerrado_en = fecha.replace(fecha[11:], "18:00:00") if estado == 'cerrada' else None
    cur.execute(A(
        "INSERT INTO historia_clinica(admision_id,paciente_id,tipo_hc,estado,"
        "motivo_consulta,causa_atencion,cod_cie10_ingreso,cod_cie10_egreso,"
        "condicion_egreso,destino_egreso,medico_nombre,firma_medico,creado_por,creado_en,cerrado_en)"
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
        (adm_id, pid, tipo_hc, estado,
         motivo, causa, cie10_in, cie10_eg,
         cond_eg, dest_eg, medico, medico, medico, fecha, cerrado_en))
    conn.commit()
    hc = core.row(cur, A("SELECT id FROM historia_clinica WHERE admision_id=?"), (adm_id,))
    hc_id = hc['id']

    # Update admission with hc_id
    cur.execute(A("UPDATE admisiones SET hc_id=? WHERE id=?"), (hc_id, adm_id))
    conn.commit()

    # Evoluciones
    for ei, ev in enumerate(evoluciones):
        enf_actual, analisis, cie10, sv_json = ev
        cur.execute(A(
            "INSERT INTO hc_evoluciones(hc_id,tipo,enfermedad_actual,analisis,"
            "plan_terapeutico,cod_cie10,signos_vitales_json,medico_nombre,creado_en)"
            "VALUES(?,?,?,?,?,?,?,?,?)"),
            (hc_id, 'evolucion', enf_actual, analisis,
             analisis, cie10, sv_json, medico, fecha))
        conn.commit()

    # Órdenes
    for o in ordenes:
        tipo_o, cups, nombre_e, cant, prio, indicacion, dx = o
        cur.execute(A(
            "INSERT INTO ordenes_medicas(hc_id,admision_id,paciente_id,tipo_orden,"
            "cod_cups,nombre_estudio,cantidad,prioridad,indicacion_clinica,"
            "diagnostico_asociado,estado,medico_ordena,creado_en)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"),
            (hc_id, adm_id, pid, tipo_o, cups, nombre_e, cant, prio, indicacion, dx,
             'completada' if estado == 'cerrada' else 'pendiente', medico, fecha))
        conn.commit()

    # Prescripciones
    for rx in rxs:
        med, cum, conc, forma, via, dosis, frec, dur, cant, instr, dx = rx
        cur.execute(A(
            "INSERT INTO prescripciones(hc_id,admision_id,paciente_id,medicamento,"
            "cod_cum,concentracion,forma_farmaceutica,via_administracion,dosis,"
            "frecuencia,duracion,cantidad_total,instrucciones,diagnostico_asociado,"
            "estado,medico_nombre,creado_en)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
            (hc_id, adm_id, pid, med, cum, conc, forma, via, dosis, frec, dur, cant,
             instr, dx, 'dispensada' if estado == 'cerrada' else 'pendiente', medico, fecha))
        conn.commit()

    # Interconsultas
    for ic in ics:
        esp, esp_name, motivo_ic, dx, prio = ic
        cur.execute(A(
            "INSERT INTO interconsultas(hc_id,admision_id,paciente_id,tipo,"
            "especialidad_solicitada,motivo,diagnostico_presuntivo,cod_cie10,"
            "prioridad,estado,medico_solicitante,creado_en)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"),
            (hc_id, adm_id, pid, 'interconsulta', esp_name, motivo_ic,
             motivo_ic, dx, prio,
             'respondida' if estado == 'cerrada' else 'solicitada', medico, fecha))
        conn.commit()

    print(f"  HC#{hc_id} ({estado}) {tipo_hc} — {nombre_p} — {cie10_in} {motivo[:50]}...")

# Final counts
print("\n=== TOTALES FINALES ===")
for t in ['pacientes','historia_clinica','hc_evoluciones','ordenes_medicas','prescripciones','interconsultas','admisiones']:
    c = core.row(cur, f'SELECT COUNT(*) as c FROM {t}')
    print(f"  {t}: {c['c']}")

cur.close()
core._return_db(conn, db)
print("\nSeed completado.")
