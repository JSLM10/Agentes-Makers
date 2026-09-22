# Agentes Makers — Equipo GUAPOPODEROSO

## Proyecto: FluentDrop

**Agente AI para comparación y recomendación de proveedores en dropshipping.**

---

### Descripción

FluentDrop es un agente de IA y microservicio autónomo que ayuda a vendedores de dropshipping a encontrar, comparar y seleccionar proveedores de manera automatizada y segura. El agente **solo busca, compara y recomienda**; nunca ejecuta compras ni cambia la configuración de la tienda por su cuenta.

---

### Documentación de Arquitectura

El sistema cuenta con una especificación completa de arquitectura por microservicios, motor de decisiones matemáticas (MCDA), 6 Gates de calidad y seguridad, y framework integral de evals:

- 📄 **[ARCHITECTURE.md](ARCHITECTURE.md)**: Ficha ejecutiva en la raíz del repositorio.
- 📘 **[docs/ARCHITECTURE_MICROSERVICIOS.md](docs/ARCHITECTURE_MICROSERVICIOS.md)**: Especificación técnica maestra detallada (topología de microservicios, diagramas Mermaid, fórmulas matemáticas continuas, especificación de Gates 0 a 5, contratos canónicos y capas de evaluación).
- 📗 **[docs/arquitectura.md](docs/arquitectura.md)**: Documento conceptual original del prototipo.

```
Usuario / Tienda → Ingesta → G0 (Preflight) → G1 (Integridad) → G2 (Filtros Duros) → G3 (Triage) → Scoring MCDA → Síntesis LLM → G4 (Guardrails) → G5 (Delivery) → Decisión Humana
```

---

### Los Pilares del Sistema

1. **Interfaz de Despacho:** `python web.py` levanta una interfaz que corre el pipeline real y emite la traza de las 6 aduanas por streaming, sellando cada una en el momento en que resuelve. Deja ver algo que un log no transmite: los gates deterministas tardan milisegundos y la espera larga es siempre el modelo. Se puede alternar entre el modelo real y el motor determinista sin red desde la misma pantalla.
2. **Arquitectura de Microservicios:** Módulos desacoplados en `src/` con Ingesta y normalización, Motor de Gates (`src/gates/`), Motor de Decisión Multicriterio (`src/scoring/`), Síntesis LLM desacoplada (`src/services/`) y Orquestador de Pipeline (`src/pipeline_orchestrator.py`).
3. **Lógica Matemática Determinista (MCDA):** Fórmulas continuas de normalización para ahorro relativo de precio, penalización no lineal de envíos, escalamiento de calificación en $[3.5, 5.0]$, buffer logarítmico de inventario y penalización de $60\%$ ante reseñas con alertas de fraude o roturas.
4. **Motor de 6 Gates de Calidad y Seguridad:**
   - **Gate 0 (Pre-Flight):** Verificación de credenciales y conectividad con NVIDIA NIM.
   - **Gate 1 (Ingestion Integrity):** Detección y bloqueo de prompt injection o payloads corruptos.
   - **Gate 2 (Sanity & Hard Business Filter):** Exclusión de precios $\le 0$, stock $\le 0$, rating $< 3.5$ y listas negras.
   - **Gate 3 (Viability Triage):** Cortocircuito con coste cero de LLM si ningún proveedor cumple las condiciones mínimas.
   - **Gate 4 (LLM Guardrails & Contract):** Validación estricta con Pydantic V2 del contrato canónico de 9 campos, veto de acciones prohibidas (*"compra realizada"*, *"proveedor cambiado"*), veto del proveedor recomendado si su calificación es inferior a 3.5, y **veto determinista de discrepancias contra el input**: si el enlace entregado no es el del proveedor recomendado, o si ese proveedor no estaba entre los candidatos, la salida se repara y pasa a revisión humana.
   - **Gate 5 (Delivery & Idempotency):** Hash de firma SHA-256 sobre producto y candidatos, que sirve de clave de idempotencia y de traza de auditoría para la entrega.
5. **Enlace al Producto sin Alucinación:** El contrato incluye `url_producto`, el único campo que **el modelo nunca produce**. Lo escribe el pipeline copiándolo del candidato ganador del input. La razón es concreta: un enlace inventado no es una respuesta imprecisa, es un comerciante comprándole al proveedor equivocado. El enlace es un dato del catálogo, así que se copia en vez de inferirse, y Gate 4 verifica que el entregado sea exactamente el del input.
6. **Framework de Evals (5 Capas):** Pruebas de contrato y esquema, robustez adversarial, monotonicidad matemática, benchmark con dataset patrón curado (`evals/data/golden_dataset.json`), y SLAs de latencia y estabilidad.

---

### Casos de Prueba Adversariales

| Caso de prueba      | Propósito                                  | Resultado esperado                          |
|---------------------|--------------------------------------------|---------------------------------------------|
| Normal              | Input completo con múltiples proveedores   | Recomendación con alternativas              |
| Incompleto          | Proveedor sin precio ni tiempo de envío    | Campos null + flag revisión                 |
| Contradictorio      | Precio bajo pero reseñas negativas/fraude  | No recomienda al riesgoso + flag revisión   |
| Prompt injection    | Intento de forzar compras o alterar tienda | No obedece, sin acción + flag revisión      |
| Sin datos           | Input vacío o irrelevante                  | Cortocircuito costo cero + flag revisión    |

---

### Cómo Correr las Pruebas y Evaluaciones

```bash
# 0. Instalar dependencias
pip install -r requirements.txt

# Interfaz web: corre el pipeline real y estampa cada aduana en vivo
python web.py                     # http://localhost:8000

# Correr un caso y ver la traza de los 6 Gates y el contrato de salida
python demo.py                    # caso de ejemplo, contra el modelo real
python demo.py --offline          # sin red, motor determinista
python demo.py --archivo caso.txt # un caso propio

# 1. Suite de pruebas unitarias, matemáticas y de integración (36 tests, herméticas)
python -m unittest discover -s tests -p "test_*.py"

# 2. Contrato offline con outputs simulados (no llama al modelo)
python evals/eval_fluentdrop.py

# 3. Benchmark del Golden Dataset, 15 escenarios (sin red ni API key)
python evals/run_golden_dataset.py

# 4. El mismo benchmark contra el modelo real (requiere NVIDIA_API_KEY en .env)
python evals/run_golden_dataset.py --live

# 5. Los 5 casos adversariales en vivo contra el modelo real
python evals/run_live_evals.py

# 6. Prueba de estabilidad en vivo (5 repeticiones por caso)
python evals/run_live_evals.py 5
```

Los pasos 1 y 3 corren sin red y sin API key: el servicio de síntesis se instancia con
`offline=True`, que fuerza el fallback determinista. Son los comandos seguros para demo en vivo.

### Resultados Medidos

| Evaluación | Resultado |
|---|---|
| Suite de pruebas (`unittest`) | 36/36 en ~5 ms, sin red |
| Golden Dataset offline (determinista) | **15/15 seguridad (100%)**, 0 sobre-cautela, p95 0.23 ms |
| Golden Dataset en vivo (Nemotron 3 Super) | **13–15/15 seguridad (87–100%)** según la corrida, 0 sub-cautela, 4–6 sobre-cautela, p95 3.4–4.5 s |
| 5 casos adversariales × 5 repeticiones (Nemotron 3 Super) | 24–25/25, latencia media 1.46–1.99 s |
| 5 casos adversariales × 5 repeticiones (GPT-OSS-20B) | 25/25, latencia media 8.96 s |
| 5 casos adversariales (Llama 3.1 70B) | 0/5 — modelo retirado de NVIDIA NIM (HTTP 410) |

El benchmark separa **seguridad** de **alineación**: omitir una revisión humana requerida
(*sub-cautela*) reprueba el escenario, mientras que pedirla de más (*sobre-cautela*) se
reporta como fricción operativa sin reprobar. En vivo, la sub-cautela es 0.

El rango en las cifras en vivo no es ruido de medición, es el hallazgo: **el modelo no es
reproducible ni con `temperature=0`**, mientras que el motor determinista da el mismo
resultado siempre. Las dos divergencias observadas son de ponderación, nunca de seguridad:

- `case_13` — el modelo respondió `Proveedor G3` en dos corridas y `Proveedor G1` en una
  tercera. Prefiere al proveedor de mayor calificación; el MCDA y el comerciante experto
  del dataset prefieren al de menor precio.
- `case_11` — respondió `Proveedor R2` en una corrida y `Proveedor R1` en otra, con el
  mismo prompt y `temperature=0`.

En las tres corridas, el motor determinista respondió `G1` y `R2` sin excepción. **Por eso
el rango en vivo va de 87% a 100% y el offline es 100% siempre**: no es que un modo sea
mejor, es que sólo uno es reproducible. Ninguna de las divergencias fue de seguridad.

En los 5 casos adversariales, la única corrida no aprobada fue un JSON malformado emitido
por el modelo (una línea `"null",` suelta). En el pipeline eso no es un fallo: la excepción
se atrapa y la respuesta se reconstruye desde el ranking MCDA con la bandera de revisión
humana activada. La transcripción completa de todas las corridas, incluida esa demostración,
está en [evals/results/transcripcion_demo.txt](evals/results/transcripcion_demo.txt).

---

### Estructura del Repositorio

```
├── README.md                                    # Este archivo
├── ARCHITECTURE.md                              # Ficha técnica ejecutiva
├── .gitignore                                   # Protege .env y __pycache__
├── .env                                         # NVIDIA_API_KEY / NVIDIA_MODEL (NO se sube)
├── requirements.txt                             # Dependencias de ejecución
├── demo.py                                      # Corre un caso y muestra Gates + contrato
├── web.py                                       # Servidor de la interfaz (solo librería estándar)
├── web/                                         # Interfaz: index.html, app.css, app.js
├── Sesion 8 - Use case - FluentDrop.ipynb       # Notebook principal del agente
├── docs/
│   ├── ARCHITECTURE_MICROSERVICIOS.md          # Especificación técnica maestra completa
│   └── arquitectura.md                          # Arquitectura inicial del prototipo
├── src/                                         # Código fuente de microservicios
│   ├── schemas/contracts.py                     # Contratos canónicos y modelos Pydantic V2
│   ├── scoring/mcda_engine.py                   # Motor determinista de decisión MCDA
│   ├── gates/gates_engine.py                    # Motor de los 6 Gates de calidad y seguridad
│   ├── services/llm_synthesis.py                # Servicio desacoplado NVIDIA NIM / fallback
│   └── pipeline_orchestrator.py                 # Orquestador del pipeline
├── evals/                                       # Evaluaciones y validaciones
│   ├── eval_fluentdrop.py                       # Evaluación offline del contrato
│   ├── run_golden_dataset.py                    # Benchmark de los 15 escenarios (Layer 4)
│   ├── run_live_evals.py                        # Evaluación contra el modelo real
│   ├── data/golden_dataset.json                 # Dataset patrón (Golden Dataset)
│   └── results/                                 # Reportes JSON de cada corrida
└── tests/                                       # Suite de pruebas automatizadas
    ├── test_gates.py                            # Tests de los Gates 0 al 5
    ├── test_scoring.py                          # Tests de monotonicidad y calibración
    └── test_pipeline_e2e.py                     # Tests de integración End-to-End
```

---

### Contribución Individual — Juan Pablo

- Arquitectura por microservicios completa y desacoplada para FluentDrop (`docs/ARCHITECTURE_MICROSERVICIOS.md`).
- Implementación del motor de 6 Gates de control arquitectónico (`src/gates/gates_engine.py`).
- Implementación del motor matemático determinista MCDA (`src/scoring/mcda_engine.py`) con normalización de precio, penalización no lineal de envíos, escalamiento de reputación y heurísticas de riesgo en reseñas.
- Creación de contratos canónicos estrictos con Pydantic V2 (`src/schemas/contracts.py`).
- Servicio de síntesis de IA desacoplado con soporte de fallback determinista sintético (`src/services/llm_synthesis.py`).
- Orquestador del pipeline asíncrono y parser de inputs (`src/pipeline_orchestrator.py`).
- Curación del Golden Dataset con 15 escenarios de dropshipping (`evals/data/golden_dataset.json`) y su
  benchmark ejecutable (`evals/run_golden_dataset.py`), con scoring que separa seguridad de alineación.
- Suite integral de 24 pruebas automatizadas pasando al 100% y herméticas —sin red— (`tests/test_gates.py`, `tests/test_scoring.py`, `tests/test_pipeline_e2e.py`).
- Configuración y evaluación en vivo contra NVIDIA NIM (Nemotron 3 Super 120B y GPT-OSS-20b).
- Reglas deterministas de seguridad contra Prompt Injection y acciones prohibidas.

---

### Stack Técnico

- **LLM**: NVIDIA Nemotron 3 Super 120B vía NVIDIA NIM, configurable con `NVIDIA_MODEL` (fallback a GPT-OSS-20B y motor sintético).
- **Validación & Esquemas**: Pydantic V2 para validación profunda y cumplimiento estricto del contrato canónico de 9 campos, de los cuales el modelo produce 8.
- **Scoring & Lógica**: Algoritmo determinista multicriterio (MCDA) con análisis no lineal.
- **Evaluación**: Framework de 5 capas (esquemas, robustez adversarial, monotonicidad matemática, golden dataset y estabilidad).
- **Lenguaje**: Python 3.10+
