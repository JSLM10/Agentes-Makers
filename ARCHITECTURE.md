# FluentDrop — Arquitectura de Microservicios & Especificaciones

Este repositorio contiene el sistema inteligente de comparación y recomendación de proveedores de dropshipping (**FluentDrop**).

---

## 📌 Documento Maestro de Arquitectura

El diseño técnico completo de la arquitectura por microservicios, el framework de **Evals**, la **Lógica de Decisión Multicriterio (MCDA)** y el motor de **6 Gates** de calidad y seguridad se encuentra documentado en detalle en:

👉 **[`docs/ARCHITECTURE_MICROSERVICIOS.md`](docs/ARCHITECTURE_MICROSERVICIOS.md)**

---

## 🏛️ Resumen de los 4 Pilares del Sistema

### 1. Arquitectura de Microservicios Desacoplada
- **Ingestion & Normalizer Service:** Captura datos desde webhooks de Shopify/WooCommerce, extractores de catálogos (Dropi, CJ, AliExpress) o payloads JSON manuales. Estandariza precios a USD y tiempos de envío a días numéricos.
- **Broker & Pipeline Asíncrono:** Comunicación orientada a eventos con colas especializadas (`supplier.ingest.raw`, `supplier.scoring.task`, `supplier.synthesis.task`, `supplier.recommendation.ready`) y Dead Letter Queue (DLQ) para auditoría.
- **Orquestador Central:** Coordina la ejecución secuencial gobernada por Gates y degradación elegante ante caídas de proveedores de IA.

### 2. Lógica Matemática de Decisión Multicriterio (MCDA)
- **Normalización Continua:** Fórmulas deterministas para evaluar:
  - $\Delta P$ (Ahorro relativo de precio frente al proveedor actual).
  - Penalización logística no lineal para envíos que superan 7 y 25 días.
  - Escalamiento estricto de reputación (rating) en el rango $[3.5, 5.0]$.
  - Buffer logarítmico de seguridad de stock para prevenir quiebres de inventario.
- **Ponderación Global:**
  $$Score_{MCDA} = 0.35 \cdot S_{precio} + 0.30 \cdot S_{envio} + 0.25 \cdot S_{rating} + 0.10 \cdot S_{stock}$$
- **Detector de Riesgo Heurístico:** Filtro semántico de quejas críticas en reseñas (*"estafa"*, *"paquete nunca llegó"*, *"falso tracking"*) que aplica penalización de $60\%$ y activa revisión humana obligatoria.

### 3. Motor de 6 Gates de Calidad y Seguridad
- **Gate 0 (Pre-Flight):** Verificación de credenciales de NVIDIA NIM, salud del endpoint y rate-limiting.
- **Gate 1 (Ingestion Integrity):** Sanitización contra prompt injection, límites de longitud y verificación de payload.
- **Gate 2 (Sanity & Hard Business Filter):** Exclusión de precios $\le 0$, stock $\le 0$, proveedores con rating $< 3.5$ y listas negras.
- **Gate 3 (Viability Triage):** Cortocircuito con costo cero de LLM si ningún candidato cumple las condiciones mínimas.
- **Gate 4 (LLM Guardrails & Contract Enforcement):** Validación Pydantic V2 del contrato inmutable de 8 campos, detección de acciones prohibidas (*"compra realizada"*, *"proveedor cambiado"*) y veto determinista del proveedor recomendado con calificación inferior a 3.5.
- **Gate 5 (Delivery & Idempotency):** Hash SHA-256 de producto y candidatos como clave de idempotencia, traza de auditoría y entrega al comerciante.

### 4. Framework Integral de Evals (5 Capas)
1. **Layer 1 (Schema & Type Fidelity):** 100% de apego a los 8 campos y esquemas anidados.
2. **Layer 2 (Adversarial Robustness):** Cero acatamiento de órdenes de compra inyectadas.
3. **Layer 3 (Monotonicidad MCDA):** Pruebas matemáticas de sensibilidad de gradientes.
4. **Layer 4 (Golden Dataset):** Benchmark de 25+ casos de dropshipping con decisiones de comerciantes expertos.
5. **Layer 5 (SLA & Estabilidad):** Latencia p95 $< 2.5\text{s}$ con NVIDIA Nemotron 3 Super, cero claves JSON duplicadas y alta disponibilidad con fallback secundario (`gpt-oss-20b` / determinista).

---

## 📂 Estructura del Código

```
├── ARCHITECTURE.md                             # Este documento ejecutivo
├── README.md                                   # Guía general del proyecto
├── docs/
│   ├── ARCHITECTURE_MICROSERVICIOS.md          # Especificación técnica maestra
│   └── arquitectura.md                         # Documento de arquitectura conceptual
├── src/
│   ├── schemas/contracts.py                    # Modelos canónicos Pydantic V2
│   ├── scoring/mcda_engine.py                  # Motor determinista de cálculo MCDA
│   ├── gates/gates_engine.py                   # Implementación de los 6 Gates
│   ├── services/llm_synthesis.py               # Servicio desacoplado NVIDIA NIM / fallback
│   └── pipeline_orchestrator.py                # Orquestador del pipeline
├── evals/
│   ├── eval_fluentdrop.py                      # Evaluador offline del contrato
│   ├── run_live_evals.py                       # Evaluador en vivo contra NVIDIA NIM
│   ├── data/golden_dataset.json                # Dataset patrón curado
│   └── results/                                # Reportes JSON de corridas
└── tests/
    ├── test_gates.py                           # Tests unitarios de los Gates 0 al 5
    ├── test_scoring.py                         # Tests de calibración y monotonicidad
    └── test_pipeline_e2e.py                    # Tests de integración end-to-end
```
