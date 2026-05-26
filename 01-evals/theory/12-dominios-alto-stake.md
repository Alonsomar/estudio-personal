# 12 — Bonus: dominios alto-stake (legal/fiscal)

## Por qué este dominio es diferente

Todo lo que hemos construido en las secciones 1-11 aplica a cualquier sistema RAG.
Pero cuando el dominio es **regulatorio, legal o fiscal**, los estándares cambian
cualitativamente. Un error en un chatbot de recetas tiene costo bajo; un error en
un sistema que cita normativa chilena para informar decisiones fiscales puede tener
consecuencias legales, financieras y reputacionales graves.

**Analogía económica:** es la diferencia entre un error en un forecast del PIB
(impacto: paper con pie de página de corrección) y un error en una declaración de
impuestos (impacto: multas, intereses, sanción del SII). Mismo tipo de modelo,
consecuencias radicalmente distintas.

```mermaid
graph TD
    subgraph "Dominio genérico"
        E1["Error en respuesta"]
        E1 --> C1["Usuario insatisfecho"]
        C1 --> I1["Costo: bajo<br/>Reformulación, churn"]
    end
    
    subgraph "Dominio alto-stake (fiscal)"
        E2["Error en respuesta"]
        E2 --> C2a["Cita normativa<br/>incorrecta"]
        E2 --> C2b["Monto o plazo<br/>incorrecto"]
        E2 --> C2c["Omisión de<br/>obligación"]
        C2a --> I2["Costo: alto<br/>Decisión fiscal errónea,<br/>multas, responsabilidad legal"]
        C2b --> I2
        C2c --> I2
    end
    
    style I1 fill:#ffe,stroke:#cc3
    style I2 fill:#fee,stroke:#c33
```

## Los 5 fallos críticos en dominio fiscal

### 1. Alucinación normativa

El fallo más peligroso: el sistema **inventa** una norma, artículo o circular que
no existe, o atribuye contenido incorrecto a una norma real.

| Tipo | Ejemplo | Gravedad |
|------|---------|----------|
| **Norma inventada** | "Según la Circular Nº 87 del SII..." (no existe) | Crítica |
| **Artículo mal citado** | "Art. 12 de la Ley 20.730" (el artículo dice otra cosa) | Crítica |
| **Contenido transpuesto** | Cita correcta pero contenido de otra norma | Alta |
| **Vigencia incorrecta** | Cita norma derogada como vigente | Alta |
| **Jurisdicción cruzada** | Aplica norma española a caso chileno | Media |

```mermaid
graph LR
    A["Alucinación<br/>normativa"]
    A --> T1["Norma<br/>inventada"]
    A --> T2["Artículo<br/>mal citado"]
    A --> T3["Contenido<br/>transpuesto"]
    A --> T4["Vigencia<br/>incorrecta"]
    A --> T5["Jurisdicción<br/>cruzada"]
    
    style T1 fill:#fee,stroke:#c33
    style T2 fill:#fee,stroke:#c33
    style T3 fill:#ffe,stroke:#cc3
    style T4 fill:#ffe,stroke:#cc3
    style T5 fill:#eef,stroke:#66c
```

### 2. Cita fantasma

El sistema cita un documento o artículo específico que **no aparece en los documentos
recuperados** por el retriever. Es un subset de la alucinación normativa, pero
particularmente insidioso porque parece preciso.

**Eval específica:** para cada cita en la respuesta, verificar que:
1. El documento citado fue recuperado (está en `retrieved_docs`)
2. El contenido citado aparece en ese documento
3. La interpretación del contenido es correcta

### 3. Omisión de obligación

El sistema responde correctamente **lo que dice**, pero omite información crítica
que debería incluir. En dominio fiscal, una omisión puede ser tan dañina como un error.

| Ejemplo | Qué dice | Qué omite |
|---------|----------|-----------|
| "El IVA digital es 19%" | Correcto | No menciona que el plazo de declaración es el día 12 del mes siguiente |
| "La multa es 10-50 UTM" | Correcto | No menciona que hay reincidencia con agravante |
| "La Glosa 09 cubre operaciones" | Correcto | No menciona los requisitos de reporte trimestral |

### 4. Confianza no calibrada

El sistema presenta todas sus respuestas con el mismo nivel de confianza, sin distinguir
entre:
- Hechos extraídos directamente del texto normativo (alta confianza)
- Interpretaciones derivadas de múltiples fuentes (confianza media)
- Inferencias sin respaldo directo en el corpus (baja confianza)

### 5. Formato inadecuado para el usuario

En dominio legal/fiscal, el **formato** importa tanto como el contenido. Un analista
fiscal espera:
- Citas con referencia exacta (ley, artículo, inciso, literal)
- Montos con unidad (UTM, UF, CLP)
- Plazos con fecha específica o referencia temporal clara
- Distinción entre norma general y excepciones

## Evals específicas para alto-stake

### Eval 1: Verificación de citas normativas

Para cada respuesta que cite una norma:

```mermaid
graph TD
    R["Respuesta con cita:<br/>'Según Art. 3 Ley 20.730...'"]
    R --> V1{"¿El documento<br/>fue recuperado?"}
    V1 -- No --> F1["❌ CITA FANTASMA<br/>Severidad: crítica"]
    V1 -- Sí --> V2{"¿El artículo<br/>existe en el doc?"}
    V2 -- No --> F2["❌ ARTÍCULO<br/>INEXISTENTE"]
    V2 -- Sí --> V3{"¿El contenido<br/>citado coincide?"}
    V3 -- No --> F3["❌ CONTENIDO<br/>INCORRECTO"]
    V3 -- Sí --> V4{"¿La interpretación<br/>es fiel?"}
    V4 -- No --> F4["⚠️ INTERPRETACIÓN<br/>SESGADA"]
    V4 -- Sí --> OK["✅ CITA VERIFICADA"]
    
    style F1 fill:#fee,stroke:#c33
    style F2 fill:#fee,stroke:#c33
    style F3 fill:#fee,stroke:#c33
    style F4 fill:#ffe,stroke:#cc3
    style OK fill:#efe,stroke:#3c3
```

**Métrica:** `citation_accuracy = citas_verificadas / total_citas`

Umbral recomendado: **≥ 0.95** (en dominio genérico, ≥ 0.80 es aceptable).

### Eval 2: Completitud de obligaciones

Para queries sobre obligaciones fiscales, verificar que la respuesta menciona
**todos** los elementos obligatorios:

| Elemento | Ejemplo (IVA digital) | Obligatorio |
|----------|----------------------|-------------|
| Tasa aplicable | 19% | Sí |
| Sujeto obligado | Prestador extranjero | Sí |
| Plazo de declaración | Día 12 del mes siguiente | Sí |
| Base imponible | Precio del servicio | Sí |
| Excepciones | Servicios B2B con reverse charge | Sí, si aplica |
| Sanciones por incumplimiento | Multas Art. 97 CT | Deseable |

**Métrica:** `obligation_completeness = elementos_mencionados / elementos_obligatorios`

Umbral recomendado: **≥ 0.80** para elementos obligatorios, sin umbral para deseables.

### Eval 3: Abstención calibrada

El sistema debe **decir "no sé"** cuando:
- La query está fuera del alcance del corpus
- El corpus no contiene información suficiente para responder
- La respuesta requiere interpretación jurídica que excede lo factual

```mermaid
graph TD
    Q["Query del usuario"]
    Q --> C1{"¿Está dentro<br/>del dominio?"}
    C1 -- No --> A1["Responder:<br/>'Fuera de alcance'<br/>✓ Abstención correcta"]
    C1 -- Sí --> C2{"¿Hay docs<br/>relevantes?"}
    C2 -- No --> A2["Responder:<br/>'No tengo información<br/>suficiente'<br/>✓ Abstención correcta"]
    C2 -- Sí --> C3{"¿La respuesta<br/>requiere interpretación<br/>jurídica?"}
    C3 -- Sí --> A3["Responder con<br/>disclaimer:<br/>'Según el texto...<br/>pero consulte a un<br/>profesional'<br/>✓ Abstención parcial"]
    C3 -- No --> A4["Responder<br/>normalmente<br/>✓ Respuesta"]
    
    style A1 fill:#eef,stroke:#66c
    style A2 fill:#eef,stroke:#66c
    style A3 fill:#ffe,stroke:#cc3
    style A4 fill:#efe,stroke:#3c3
```

**Métricas de abstención:**

| Métrica | Fórmula | Qué mide |
|---------|---------|----------|
| **Abstention rate** | abstenciones / total_queries | ¿Con qué frecuencia dice "no sé"? |
| **Correct abstention** | abstenciones_correctas / abstenciones | ¿Cuándo dice "no sé", tiene razón? |
| **Missed abstention** | debió_abstenerse_y_no_lo_hizo / total | Lo más peligroso: responde cuando no debería |
| **False abstention** | se_abstuvo_innecesariamente / total | Menos grave: dice "no sé" cuando sí sabe |

**El error asimétrico:** en dominio fiscal, una **missed abstention** (responder con
confianza algo incorrecto) es mucho peor que una **false abstention** (decir "no sé"
cuando podría haber respondido). El umbral debe reflejar esta asimetría:

- Missed abstention: **< 2%** (casi cero tolerancia)
- False abstention: **< 15%** (tolerable, preferible errar hacia la cautela)

### Eval 4: Consistencia temporal

La normativa cambia. Una respuesta correcta en enero puede ser incorrecta en julio
si hubo una modificación legal. El sistema debe:

1. **No citar normas derogadas** como vigentes
2. **Indicar la fecha de vigencia** cuando es relevante
3. **Alertar sobre cambios recientes** si el corpus fue actualizado

**Métrica:** `temporal_accuracy = respuestas_con_vigencia_correcta / total_respuestas`

### Eval 5: Formato y estructura

```mermaid
graph TD
    F["Formato de respuesta"]
    F --> F1["Citas con referencia<br/>exacta (Ley, Art., Inc.)"]
    F --> F2["Montos con unidad<br/>(UTM, UF, CLP, %)"]
    F --> F3["Plazos específicos<br/>(día, mes, condición)"]
    F --> F4["Distinción norma<br/>general vs excepción"]
    F --> F5["Disclaimer cuando<br/>aplica"]
```

**Checklist de formato (evaluar por respuesta):**

| Criterio | Peso | Ejemplo correcto | Ejemplo incorrecto |
|----------|------|------------------|--------------------|
| Cita con referencia | 0.25 | "Art. 3 inc. 2 Ley 20.730" | "según la ley de lobby" |
| Monto con unidad | 0.20 | "10 a 50 UTM" | "una multa significativa" |
| Plazo específico | 0.20 | "hasta el día 12 del mes siguiente" | "en los próximos días" |
| Excepción mencionada | 0.20 | "salvo cuando el prestador..." | (omisión) |
| Disclaimer si aplica | 0.15 | "Consulte a un profesional" | (afirmación categórica sobre interpretación) |

## Umbrales diferenciados

Los umbrales de las secciones 9 (gates) deben ser **más estrictos** en dominio
alto-stake:

| Métrica | Dominio genérico | Dominio fiscal | Por qué |
|---------|-----------------|----------------|---------|
| Faithfulness | ≥ 0.50 | ≥ 0.70 | Cada claim debe tener respaldo |
| Citation accuracy | ≥ 0.80 | ≥ 0.95 | Citas incorrectas = responsabilidad |
| Ghost citations | < 5% | **= 0** | Zero tolerance |
| Missed abstention | < 10% | **< 2%** | Error asimétrico |
| Obligation completeness | ≥ 0.60 | ≥ 0.80 | Omisiones tienen costo legal |
| Format compliance | ≥ 0.50 | ≥ 0.75 | El usuario profesional exige precisión |

```mermaid
graph LR
    subgraph "Gates genéricos (sección 9)"
        G1["Faithfulness ≥ 0.50"]
        G2["Ghost citations < 5%"]
        G3["Recall@5 ≥ 0.60"]
    end
    
    subgraph "Gates fiscales (esta sección)"
        F1["Faithfulness ≥ 0.70"]
        F2["Ghost citations = 0"]
        F3["Recall@5 ≥ 0.60"]
        F4["Citation accuracy ≥ 0.95"]
        F5["Missed abstention < 2%"]
        F6["Obligation completeness ≥ 0.80"]
    end
    
    G1 -.->|"más estricto"| F1
    G2 -.->|"zero tolerance"| F2
    G3 -.->|"igual"| F3
    
    style F2 fill:#fee,stroke:#c33
    style F5 fill:#fee,stroke:#c33
```

## Implicaciones regulatorias

### Marco legal en Chile (2025-2026)

- **No hay regulación específica de IA** en Chile (a diferencia de la EU AI Act).
  El Proyecto de Ley Marco de IA (Boletín 15.869) está en trámite legislativo.
- **Responsabilidad civil** sigue el régimen general: el proveedor del sistema
  puede ser responsable por daños derivados de información incorrecta.
- **Sector financiero:** la CMF ha emitido lineamientos sobre uso de IA en entidades
  supervisadas, exigiendo explicabilidad y auditoría.
- **SII:** no hay pronunciamiento específico sobre uso de IA para asesoría tributaria,
  pero el contribuyente es responsable de la información en sus declaraciones.

### Recomendaciones prácticas

1. **Disclaimer obligatorio:** toda respuesta del sistema debe incluir que no
   constituye asesoría legal o tributaria profesional
2. **Trazabilidad:** cada respuesta debe ser rastreable a sus fuentes documentales
3. **Auditoría:** mantener logs completos (sección 11) para revisión posterior
4. **Actualización del corpus:** establecer un SLA de actualización cuando cambia
   la normativa (e.g., < 48h para circulares del SII)
5. **Revisión humana:** para decisiones de alto impacto, el sistema debe recomendar
   consulta profesional

## Golden dataset para alto-stake

El golden dataset (sección 4) necesita items específicos para este dominio:

### Categorías adicionales

| Categoría | Descripción | Items mínimos |
|-----------|-------------|---------------|
| **Citas verificables** | Queries donde la respuesta debe citar norma exacta | 15-20 |
| **Obligaciones completas** | Queries sobre obligaciones con múltiples elementos | 10-15 |
| **Fuera de dominio** | Queries que el sistema debe rechazar | 10 |
| **Interpretación vs hecho** | Queries donde la respuesta requiere disclaimer | 10 |
| **Normas derogadas** | Queries sobre normas que ya no están vigentes | 5-10 |
| **Montos y plazos** | Queries donde la precisión numérica es crítica | 10-15 |

### Anotación enriquecida

Cada item del golden dataset fiscal necesita campos adicionales:

```json
{
  "query": "¿Cuál es la multa por no registrar reuniones en el registro de lobby?",
  "expected_answer": "Multa de 10 a 50 UTM según Art. 8 Ley 20.730",
  "relevant_docs": ["norma-01-ley-lobby.txt"],
  "citations_required": [
    {"law": "Ley 20.730", "article": "Art. 8", "content_fragment": "multa de 10 a 50 UTM"}
  ],
  "obligations": ["registrar", "plazo 30 días", "sanción por incumplimiento"],
  "should_abstain": false,
  "requires_disclaimer": false,
  "difficulty": "medium",
  "numerical_precision": {"value": "10-50", "unit": "UTM"}
}
```

## Protocolo de eval para alto-stake

```mermaid
graph TD
    E["Eval alto-stake"]
    E --> S1["1. Eval estándar<br/>(secciones 5-9)"]
    S1 --> S2["2. Verificación<br/>de citas"]
    S2 --> S3["3. Completitud<br/>de obligaciones"]
    S3 --> S4["4. Test de<br/>abstención"]
    S4 --> S5["5. Precisión<br/>numérica"]
    S5 --> S6["6. Formato<br/>y estructura"]
    S6 --> G{"¿Todos los<br/>gates pasan?"}
    G -- Sí --> D["Deploy con<br/>disclaimer"]
    G -- No --> R["Revisión humana<br/>obligatoria"]
    
    style R fill:#fee,stroke:#c33
    style D fill:#efe,stroke:#3c3
```

## Conexión con todas las secciones

| Sección | Conexión con alto-stake |
|---------|------------------------|
| 1. Por qué evals | Los fallos silenciosos son más costosos en este dominio |
| 2. Taxonomía | Necesitas más tipos de eval (citas, abstención, formato) |
| 3. Errores | La taxonomía de errores incluye alucinación normativa y omisión |
| 4. Golden dataset | Items adicionales para citas, abstención, precisión numérica |
| 5. Retrieval | Recall es crítico: no recuperar la norma correcta es inaceptable |
| 6. Generación | Faithfulness tiene umbral más alto (≥ 0.70 vs ≥ 0.50) |
| 7. LLM-as-judge | El juez necesita rúbrica específica para dominio fiscal |
| 8. Estadística | CIs más estrictos; el costo del error tipo II es mayor |
| 9. CI | Gates adicionales (ghost citations = 0, missed abstention < 2%) |
| 10. Costo | ROI de las evals es mayor (costo del fallo > $5,000) |
| 11. Online | Redacción de PII fiscal (RUT, montos) es obligatoria |

## Estado del arte (2025-2026)

- **Evals para legal/fiscal** están en estado **incipiente**. No hay benchmarks
  estándar ni frameworks específicos. Cada equipo construye lo suyo.
- **Verificación de citas** es un problema abierto. Los LLMs son buenos generando
  citas plausibles pero incorrectas — exactamente el peor caso para este dominio.
- **Abstención calibrada** está mejor estudiada en medicina (modelos que dicen
  "consulte a su médico") que en legal/fiscal.
- **La EU AI Act** clasifica los sistemas de IA para legal como "alto riesgo",
  requiriendo transparencia, supervisión humana y evaluación de conformidad.
  Chile aún no tiene equivalente, pero la tendencia regulatoria es clara.
- **Oportunidad:** un framework de evals riguroso para dominio fiscal chileno
  sería diferenciador competitivo significativo. La mayoría de productos en este
  espacio operan sin evaluación formal.
