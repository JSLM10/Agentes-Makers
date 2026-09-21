"""
contracts.py — Modelos canónicos y contratos de datos para FluentDrop (Pydantic V2)
===================================================================================

Define los contratos de entrada, salida inmutable de 8 campos, estructuras intermedias
de evaluación y resultados de Gates.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator


# --- Contrato de producto (campos requeridos del output de 8 campos) ---
REQUIRED_FIELDS = {
    "producto",
    "proveedor_recomendado",
    "precio",
    "tiempo_envio_estimado",
    "calificacion",
    "stock_disponible",
    "alternativas",
    "requiere_revision_humana",
}

# --- Restricciones de seguridad: Acciones prohibidas ---
FORBIDDEN_ACTIONS = [
    "compra realizada",
    "proveedor cambiado",
    "configuración actualizada",
    "configuracion actualizada",
    "orden ejecutada",
    "contactado al proveedor",
    "compra ejecutada",
    "orden confirmada",
]

# --- Regla de negocio: calificación mínima para recomendar un proveedor ---
MIN_CALIFICACION_RECOMENDADA = 3.5


class SupplierCandidate(BaseModel):
    """Proveedor candidato extraído o provisto al sistema."""
    nombre: str = Field(..., description="Nombre o identificador del proveedor")
    precio: Optional[float] = Field(None, ge=0.0, description="Precio unitario en USD")
    tiempo_envio_estimado: Optional[str] = Field(None, description="Rango de días o texto de envío (ej. '8-12 días')")
    tiempo_envio_dias: Optional[float] = Field(None, ge=0.0, description="Días promedio de envío normalizados")
    calificacion: Optional[float] = Field(None, ge=0.0, le=5.0, description="Calificación 0 a 5")
    stock_disponible: Optional[int] = Field(None, ge=0, description="Unidades físicas en stock")
    resenas: Optional[str] = Field(None, description="Reseñas o feedback cualitativo de compradores")
    es_viable: bool = Field(True, description="Indicador si sobrevive a los filtros duros de negocio")
    motivo_exclusion: Optional[str] = Field(None, description="Razón de descarte si es inviable")


class CurrentSupplier(BaseModel):
    """Proveedor activo actual de la tienda."""
    nombre: str
    precio: float = Field(..., ge=0.0)
    tiempo_envio_estimado: Optional[str] = None
    calificacion: Optional[float] = Field(None, ge=0.0, le=5.0)
    stock_disponible: Optional[int] = Field(None, ge=0)


class ProductInput(BaseModel):
    """Entrada completa canónica provista a FluentDrop."""
    producto: str = Field(..., min_length=1, description="Nombre del producto en catálogo")
    categoria: Optional[str] = Field(None, description="Categoría taxonómica")
    proveedor_actual: Optional[CurrentSupplier] = None
    proveedores_candidatos: List[SupplierCandidate] = Field(default_factory=list)


class AlternativeSupplier(BaseModel):
    """Proveedor alternativo viable evaluado."""
    nombre: str
    precio: Optional[float] = Field(None, ge=0.0)
    calificacion: Optional[float] = Field(None, ge=0.0, le=5.0)
    razon: Optional[str] = Field(None, description="Ventaja o razón de posicionamiento")


class FluentDropOutput(BaseModel):
    """Contrato inmutable de 8 campos generado para el usuario o sistema consumidor."""
    producto: Optional[str] = Field(None, description="Producto evaluado")
    proveedor_recomendado: Optional[str] = Field(None, description="Proveedor sugerido; null si ninguno califica")
    precio: Optional[float] = Field(None, ge=0.0, description="Precio unitario en USD del recomendado")
    tiempo_envio_estimado: Optional[str] = Field(None, description="Rango de días estimado")
    calificacion: Optional[float] = Field(None, ge=0.0, le=5.0, description="Calificación 0-5")
    stock_disponible: Optional[int] = Field(None, ge=0, description="Stock disponible")
    alternativas: List[AlternativeSupplier] = Field(default_factory=list, description="Otros proveedores evaluados")
    requiere_revision_humana: bool = Field(False, description="Flag de alerta o ambigüedad")

    @model_validator(mode="after")
    def check_null_coherence(self) -> "FluentDropOutput":
        """Si falta el proveedor recomendado o campos clave son null, debe requerir revisión humana."""
        critical_fields = [
            self.proveedor_recomendado,
            self.precio,
            self.tiempo_envio_estimado,
            self.calificacion,
            self.stock_disponible,
        ]
        if any(f is None for f in critical_fields):
            self.requiere_revision_humana = True
        return self


class GateResult(BaseModel):
    """Resultado de evaluación de un Gate de calidad o seguridad."""
    gate_id: str = Field(..., description="ID del gate (ej. Gate 0, Gate 1)")
    gate_name: str = Field(..., description="Nombre descriptivo del gate")
    passed: bool = Field(..., description="True si supera la aduana, False si bloquea o alerta")
    critical: bool = Field(True, description="Si es crítico, un fallo detiene el pipeline")
    reason: Optional[str] = Field(None, description="Mensaje explicativo del estado")
    details: Dict[str, Any] = Field(default_factory=dict, description="Metadatos y diagnósticos")


class PipelineReport(BaseModel):
    """Informe integral de auditoría de la ejecución del pipeline."""
    execution_id: str
    timestamp: str
    product_name: Optional[str] = None
    all_gates_passed: bool
    gates_evaluated: List[GateResult] = Field(default_factory=list)
    output: FluentDropOutput
    latency_ms: float
    model_used: Optional[str] = None
    source_type: str = "direct_input"
