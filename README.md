# Agentes Makers — Equipo GUAPOPODEROSO

## Proyecto: FluentDrop

**Agente AI para comparación y recomendación de proveedores en dropshipping.**

### Descripción

FluentDrop es un agente de IA que ayuda a vendedores de dropshipping a encontrar
y comparar proveedores de manera automatizada. El agente **solo busca, compara y
recomienda**; nunca ejecuta compras ni cambia la configuración de la tienda.

### Arquitectura del agente

```
Usuario → Input (producto + proveedores) → Validación determinista → Modelo AI → Validación output → JSON estructurado → Decisión humana
```

### Flujo de decisión

1. El usuario proporciona datos del producto y proveedores candidatos
2. El sistema valida que los datos estén completos (determinista)
3. El modelo AI compara precio, envío, calificación y stock
4. Se genera un JSON estructurado con la recomendación
5. **El humano toma la decisión final** (nunca el agente)

### Evaluaciones realizadas

| Caso de prueba      | Propósito                                  | Resultado esperado          |
|---------------------|--------------------------------------------|-----------------------------|
| Normal              | Input completo con múltiples proveedores   | Recomendación con ranking   |
| Incompleto          | Proveedor sin precio ni tiempo de envío    | Campos null + flag revisión |
| Contradictorio      | Precio bajo pero reseñas negativas         | Alerta de riesgo            |
| Prompt injection    | Intento de saltarse restricciones          | Rechazo, sin acción         |
| Sin datos           | Input vacío o irrelevante                  | Solicitar información       |

### Estructura del repositorio

```
├── README.md                                    # Este archivo
├── .gitignore                                   # Protege .env con API keys
├── .env                                         # Variables de entorno (NO se sube)
├── Sesion 8 - Use case - FluentDrop.ipynb       # Notebook principal del agente
├── Sesión 8 - Use case - MailTriage.ipynb       # Notebook caso MailTriage
├── MailTriage_Presentacion.pptx                 # Presentación del caso
└── evals/                                       # Evaluaciones y validaciones
    └── eval_fluentdrop.py                       # Script de evaluación del contrato
```

### Contribución individual — Juan Pablo

- Configuración del entorno y API key de NVIDIA NIM
- Definición del caso de uso FluentDrop (usuario, JTBD, evidencia)
- Implementación de evaluaciones adversariales (5 casos de prueba)
- Validación de contrato JSON (contract_check)
- Documentación del proyecto y estructura del repo

### Estándar de ramas

| Rama              | Uso                                    |
|-------------------|----------------------------------------|
| `dev/juanpablo`   | Trabajo individual de Juan Pablo       |
| `main`            | Proyecto integrado del equipo          |
| `makers/review`   | Feedback técnico y retos del curso     |

### Stack técnico

- **LLM**: Meta LLaMA 3.1 70B via NVIDIA NIM
- **Validación**: Pydantic models para input/output
- **Evaluación**: Casos adversariales + contract check
- **Lenguaje**: Python 3.x
