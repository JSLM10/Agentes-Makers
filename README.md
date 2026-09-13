# Agentes Makers — Equipo GUAPOPODEROSO

## Proyecto: FluentDrop

**Agente AI para comparación y recomendación de proveedores en dropshipping.**

### Descripción

FluentDrop es un agente de IA que ayuda a vendedores de dropshipping a encontrar
y comparar proveedores de manera automatizada. El agente **solo busca, compara y
recomienda**; nunca ejecuta compras ni cambia la configuración de la tienda.

### Arquitectura

La arquitectura completa (problema, input, rol de la IA, validaciones del código,
output, riesgos y evidencia) está en [docs/arquitectura.md](docs/arquitectura.md).

```
Usuario → Input (producto + proveedores) → Validación determinista → Modelo AI → Validación output → JSON estructurado → Decisión humana
```

### Flujo de decisión

1. El usuario proporciona datos del producto y proveedores candidatos
2. El sistema valida que los datos estén completos (determinista)
3. El modelo AI compara precio, envío, calificación y stock
4. Se genera un JSON estructurado con la recomendación
5. **El humano toma la decisión final** (nunca el agente)

### Evaluaciones

| Caso de prueba      | Propósito                                  | Resultado esperado                          |
|---------------------|--------------------------------------------|---------------------------------------------|
| Normal              | Input completo con múltiples proveedores   | Recomendación con alternativas              |
| Incompleto          | Proveedor sin precio ni tiempo de envío    | Campos null + flag revisión                 |
| Contradictorio      | Precio bajo pero reseñas negativas         | No recomienda al riesgoso + flag revisión   |
| Prompt injection    | Intento de saltarse restricciones          | No obedece, sin acción + flag revisión      |
| Sin datos           | Input vacío o irrelevante                  | Sin recomendación + flag revisión           |

Cómo correrlas:

```bash
# Contrato con outputs simulados (no llama al modelo)
python evals/eval_fluentdrop.py

# Los 5 casos contra el modelo real (requiere NVIDIA_API_KEY en .env)
python evals/run_live_evals.py
```

Cada corrida guarda un reporte JSON en `evals/results/`. El análisis de
resultados está en [docs/arquitectura.md](docs/arquitectura.md).

### Estructura del repositorio

```
├── README.md                                    # Este archivo
├── .gitignore                                   # Protege .env y __pycache__
├── .env                                         # NVIDIA_API_KEY / NVIDIA_MODEL (NO se sube)
├── Sesion 8 - Use case - FluentDrop.ipynb       # Notebook principal del agente
├── Sesión 8 - Use case - MailTriage.ipynb       # Notebook caso MailTriage
├── MailTriage_Presentacion.pptx                 # Presentación del caso
├── docs/
│   └── arquitectura.md                          # Arquitectura de la solución
└── evals/                                       # Evaluaciones y validaciones
    ├── eval_fluentdrop.py                       # Evaluación offline del contrato
    ├── run_live_evals.py                        # Evaluación contra el modelo real
    └── results/                                 # Reportes JSON de cada corrida
```

### Contribución individual — Juan Pablo

- Configuración del entorno y API key de NVIDIA NIM
- Definición del caso de uso FluentDrop (usuario, JTBD, evidencia)
- Implementación de evaluaciones adversariales (5 casos de prueba)
- Validación de contrato JSON (contract_check)
- Documentación del proyecto y estructura del repo
- Documentación de la arquitectura de la solución (`docs/arquitectura.md`)
- Detección del retiro de LLaMA 3.1 70B en NVIDIA NIM y migración del notebook a Nemotron 3 Super
- Evaluación en vivo de los 5 casos contra el modelo real y comparación entre modelos
- Regla determinista de calificación mínima para el proveedor recomendado

### Estándar de ramas

| Rama              | Uso                                    |
|-------------------|----------------------------------------|
| `dev/juanpablo`   | Trabajo individual de Juan Pablo       |
| `main`            | Proyecto integrado del equipo          |
| `makers/review`   | Feedback técnico y retos del curso     |

### Stack técnico

- **LLM**: NVIDIA Nemotron 3 Super 120B via NVIDIA NIM, configurable con `NVIDIA_MODEL`
  (antes Meta LLaMA 3.1 70B, retirado de NVIDIA NIM el 2026-08-26)
- **Validación**: Pydantic para la evaluación y el contrato del notebook; checks deterministas del output en `evals/`
- **Evaluación**: Casos adversariales + contract check + reglas de negocio
- **Lenguaje**: Python 3.x
