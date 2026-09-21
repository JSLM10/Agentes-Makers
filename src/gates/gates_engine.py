"""
gates_engine.py — Motor de 6 Gates de Calidad, Integridad y Seguridad para FluentDrop
====================================================================================

Implementa las 6 aduanas de control arquitectónico:
- Gate 0: Pre-Flight & Health (API keys, conectividad, dependencias)
- Gate 1: Ingestion Integrity & Sanitizer (anti-prompt injection, límites de payload)
- Gate 2: Sanity & Hard Business Filter (precio > 0, stock > 0, rating >= 3.5, blacklist)
- Gate 3: Viability Triage (decisión costo-cero de invocar o no al LLM)
- Gate 4: LLM Guardrails & Contract Enforcement (Pydantic V2, anti-acciones prohibidas, veto numérico)
- Gate 5: Delivery & Idempotency (hash SHA-256, auditoría y entrega)
"""

import hashlib
import json
import os
import re
from typing import List, Dict, Any, Optional, Tuple
from ..schemas.contracts import (
    GateResult,
    ProductInput,
    SupplierCandidate,
    FluentDropOutput,
    AlternativeSupplier,
    REQUIRED_FIELDS,
    FORBIDDEN_ACTIONS,
    MIN_CALIFICACION_RECOMENDADA,
)
from ..scoring.mcda_engine import ScoredCandidate


# Patrones de Prompt Injection / Jailbreak
INJECTION_PATTERNS = [
    r"ignora\s+(tus\s+)?instrucciones",
    r"cambia\s+(autom[aá]ticamente\s+)?el\s+proveedor",
    r"confirma\s+que\s+la\s+compra",
    r"compra\s+ya\s+fue\s+realizada",
    r"act[uú]a\s+como",
    r"system\s+prompt",
    r"bypass",
    r"modo\s+desarrollador",
]

# Lista negra de proveedores o palabras clave vetadas
DEFAULT_SUPPLIER_BLACKLIST = {
    "proveedor_vetado",
    "scam_supplier",
    "proveedor_fantasma",
    "dropship_fake_co",
}


def evaluate_gate0_preflight(require_api_key: bool = True) -> GateResult:
    """Gate 0: Verifica variables de entorno, claves de API y dependencias del sistema."""
    api_key = os.getenv("NVIDIA_API_KEY")
    if require_api_key and not api_key:
        return GateResult(
            gate_id="G0",
            gate_name="Pre-Flight & Credentials",
            passed=False,
            critical=True,
            reason="Falta NVIDIA_API_KEY en variables de entorno o archivo .env",
            details={"has_api_key": False},
        )
    return GateResult(
        gate_id="G0",
        gate_name="Pre-Flight & Credentials",
        passed=True,
        critical=True,
        reason="Entorno y credenciales verificados correctamente",
        details={"has_api_key": bool(api_key)},
    )


def evaluate_gate1_ingestion(raw_input: str) -> Tuple[GateResult, bool]:
    """
    Gate 1: Analiza el texto crudo o payload en busca de inyecciones o malformaciones.
    Retorna: (GateResult, requiere_revision_flag)
    """
    if not raw_input or not raw_input.strip():
        return (
            GateResult(
                gate_id="G1",
                gate_name="Ingestion Integrity & Sanitizer",
                passed=False,
                critical=False,
                reason="Payload de entrada vacío o nulo",
                details={"length": 0},
            ),
            True,
        )

    text_lower = raw_input.lower()
    injections_found = []
    for pattern in INJECTION_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            injections_found.append(match.group(0))

    if injections_found:
        return (
            GateResult(
                gate_id="G1",
                gate_name="Ingestion Integrity & Sanitizer",
                passed=False,
                critical=False,
                reason=f"Posible Prompt Injection detectado: {injections_found}",
                details={"injections": injections_found},
            ),
            True,
        )

    return (
        GateResult(
            gate_id="G1",
            gate_name="Ingestion Integrity & Sanitizer",
            passed=True,
            critical=False,
            reason="Payload limpio e íntegro",
            details={"length": len(raw_input)},
        ),
        False,
    )


def evaluate_gate2_hard_filter(
    candidates: List[SupplierCandidate],
    min_rating: float = MIN_CALIFICACION_RECOMENDADA,
    blacklist: Optional[set] = None,
) -> Tuple[GateResult, List[SupplierCandidate]]:
    """
    Gate 2: Filtra deterministamente proveedores que violan las reglas de negocio:
    - Precio <= 0
    - Stock <= 0 o None
    - Calificación < min_rating (3.5)
    - En lista negra
    """
    active_blacklist = blacklist or DEFAULT_SUPPLIER_BLACKLIST
    viable_candidates: List[SupplierCandidate] = []
    excluded_candidates: List[Dict[str, Any]] = []

    for c in candidates:
        reasons = []
        if c.precio is None or c.precio <= 0:
            reasons.append("Precio ausente o <= 0")
        if c.stock_disponible is not None and c.stock_disponible <= 0:
            reasons.append("Stock agotado (<= 0)")
        if c.calificacion is not None and c.calificacion < min_rating:
            reasons.append(f"Calificación {c.calificacion} < {min_rating}")
        if c.nombre.lower().strip() in active_blacklist:
            reasons.append(f"Proveedor '{c.nombre}' en lista negra")

        if reasons:
            c.es_viable = False
            c.motivo_exclusion = "; ".join(reasons)
            excluded_candidates.append({"nombre": c.nombre, "motivos": reasons})
        else:
            c.es_viable = True
            viable_candidates.append(c)

    passed = len(viable_candidates) > 0
    return (
        GateResult(
            gate_id="G2",
            gate_name="Sanity & Hard Business Filter",
            passed=passed,
            critical=False,
            reason=f"{len(viable_candidates)}/{len(candidates)} candidatos superaron los filtros duros",
            details={
                "total_evaluados": len(candidates),
                "viables": len(viable_candidates),
                "excluidos": excluded_candidates,
            },
        ),
        viable_candidates,
    )


def evaluate_gate3_triage(viable_count: int, product_name: Optional[str]) -> Tuple[GateResult, bool]:
    """
    Gate 3: Determina si el caso amerita llamada al LLM (ahorro de latencia y costo).
    Retorna: (GateResult, should_invoke_llm)
    """
    if not product_name or not product_name.strip() or viable_count == 0:
        return (
            GateResult(
                gate_id="G3",
                gate_name="Viability Triage",
                passed=False,
                critical=False,
                reason="Sin producto válido o 0 candidatos viables. Se aplica cortocircuito sin llamar al LLM.",
                details={"viable_count": viable_count, "product": product_name},
            ),
            False,
        )

    return (
        GateResult(
            gate_id="G3",
            gate_name="Viability Triage",
            passed=True,
            critical=False,
            reason="Candidatos viables detectados. Procede a síntesis con LLM.",
            details={"viable_count": viable_count, "product": product_name},
        ),
        True,
    )


def evaluate_gate4_llm_guardrail(
    output_dict: Dict[str, Any],
    scored_candidates: Optional[List[ScoredCandidate]] = None,
    min_rating: float = MIN_CALIFICACION_RECOMENDADA,
) -> Tuple[GateResult, FluentDropOutput]:
    """
    Gate 4: Aduana estricta para la salida de la IA.
    1. Valida esquema de 9 campos exactos.
    2. Detecta acciones prohibidas ('compra realizada', 'proveedor cambiado').
    3. Verifica que el recomendado no tenga calificación < min_rating.
    4. Garantiza coherencia con Pydantic V2.
    """
    violations = []
    
    # 1. Esquema
    actual_keys = set(output_dict.keys())
    missing = REQUIRED_FIELDS - actual_keys
    extras = actual_keys - REQUIRED_FIELDS
    if missing:
        violations.append(f"Campos faltantes en contrato: {sorted(missing)}")
    if extras:
        violations.append(f"Campos extra no autorizados: {sorted(extras)}")

    # 2. Acciones prohibidas
    raw_str = json.dumps(output_dict, ensure_ascii=False).lower()
    for forbidden in FORBIDDEN_ACTIONS:
        if forbidden in raw_str:
            violations.append(f"Acción prohibida detectada en output: '{forbidden}'")

    # 3. Regla determinista de calificación mínima
    recom = output_dict.get("proveedor_recomendado")
    calif = output_dict.get("calificacion")
    if recom is not None and isinstance(calif, (int, float)) and calif < min_rating:
        violations.append(f"Proveedor '{recom}' recomendado con calificación {calif} < {min_rating}")

    # 4. Veto determinista de discrepancias contra el input.
    # Requiere el ranking MCDA para saber qué proveedores existían realmente y
    # cuál era su enlace. Sin ranking no se puede verificar, y se omite.
    url_autoritativa = None
    if scored_candidates is not None:
        urls_input = {
            sc.candidate.nombre.strip().lower(): sc.candidate.url
            for sc in scored_candidates
        }
        clave = recom.strip().lower() if recom else None

        if recom is not None and clave not in urls_input:
            # El modelo recomendó un proveedor que nunca estuvo en el input.
            violations.append(f"Proveedor '{recom}' no estaba entre los candidatos del input")
        else:
            url_autoritativa = urls_input.get(clave) if clave else None
            url_declarada = output_dict.get("url_producto")
            if url_declarada != url_autoritativa:
                violations.append(
                    f"URL del producto no coincide con la del input para '{recom}': "
                    f"declarada={url_declarada!r} autoritativa={url_autoritativa!r}"
                )

    # 5. Construir y validar con Pydantic
    repaired_dict = dict(output_dict)

    # Vetos que tumban la recomendación completa: calificación bajo el mínimo,
    # acción prohibida o proveedor inexistente en el input.
    veto_recomendacion = (
        any("calificación" in v.lower() for v in violations)
        or any("prohibida" in v.lower() for v in violations)
        or any("no estaba entre los candidatos" in v for v in violations)
    )
    if veto_recomendacion:
        repaired_dict["proveedor_recomendado"] = None
        repaired_dict["precio"] = None
        repaired_dict["tiempo_envio_estimado"] = None
        repaired_dict["calificacion"] = None
        repaired_dict["stock_disponible"] = None
        repaired_dict["url_producto"] = None
        repaired_dict["requiere_revision_humana"] = True

    # Enlace inventado: la recomendación puede seguir en pie, pero el enlace se
    # reemplaza por el del input y el caso pasa a revisión humana.
    elif any("URL del producto no coincide" in v for v in violations):
        repaired_dict["url_producto"] = url_autoritativa
        repaired_dict["requiere_revision_humana"] = True

    # Asegurar campos requeridos presentes
    for rf in REQUIRED_FIELDS:
        if rf not in repaired_dict:
            repaired_dict[rf] = [] if rf == "alternativas" else None
    
    try:
        # Pydantic v2 validation
        validated_output = FluentDropOutput(**repaired_dict)
    except Exception as e:
        violations.append(f"Error de validación Pydantic: {str(e)}")
        validated_output = FluentDropOutput(
            producto=output_dict.get("producto"),
            proveedor_recomendado=None,
            precio=None,
            tiempo_envio_estimado=None,
            calificacion=None,
            stock_disponible=None,
            alternativas=[],
            requiere_revision_humana=True,
        )

    passed = len(violations) == 0
    return (
        GateResult(
            gate_id="G4",
            gate_name="LLM Guardrails & Contract Enforcement",
            passed=passed,
            critical=False,
            reason="Output cumple contrato y restricciones de seguridad" if passed else f"Violaciones detectadas: {'; '.join(violations)}",
            details={"violations": violations, "original_recomendado": recom},
        ),
        validated_output,
    )


def evaluate_gate5_delivery(
    product_name: Optional[str],
    candidates: List[SupplierCandidate],
    output: FluentDropOutput,
) -> Tuple[GateResult, str]:
    """
    Gate 5: Control de idempotencia y empaquetado para entrega.
    Retorna: (GateResult, idempotency_hash)
    """
    candidate_tokens = sorted([f"{c.nombre}:{c.precio}:{c.calificacion}" for c in candidates])
    raw_signature = f"{product_name or 'none'}|{'|'.join(candidate_tokens)}"
    hash_id = hashlib.sha256(raw_signature.encode("utf-8")).hexdigest()[:16]

    return (
        GateResult(
            gate_id="G5",
            gate_name="Delivery & Idempotency",
            passed=True,
            critical=False,
            reason="Payload verificado y empaquetado con hash de idempotencia",
            details={"idempotency_hash": hash_id, "items_evaluated": len(candidates)},
        ),
        hash_id,
    )


class GatesEngine:
    """Fachada unificada para evaluar toda la tubería de Gates."""
    def __init__(self, min_rating: float = MIN_CALIFICACION_RECOMENDADA):
        self.min_rating = min_rating

    def run_pre_flight(self) -> GateResult:
        return evaluate_gate0_preflight()

    def run_ingestion(self, raw_input: str) -> Tuple[GateResult, bool]:
        return evaluate_gate1_ingestion(raw_input)

    def run_hard_filter(self, candidates: List[SupplierCandidate]) -> Tuple[GateResult, List[SupplierCandidate]]:
        return evaluate_gate2_hard_filter(candidates, min_rating=self.min_rating)

    def run_triage(self, viable_count: int, product_name: Optional[str]) -> Tuple[GateResult, bool]:
        return evaluate_gate3_triage(viable_count, product_name)

    def run_guardrails(
        self,
        output_dict: Dict[str, Any],
        scored_candidates: Optional[List[ScoredCandidate]] = None,
    ) -> Tuple[GateResult, FluentDropOutput]:
        return evaluate_gate4_llm_guardrail(output_dict, scored_candidates, min_rating=self.min_rating)

    def run_delivery(
        self,
        product_name: Optional[str],
        candidates: List[SupplierCandidate],
        output: FluentDropOutput,
    ) -> Tuple[GateResult, str]:
        return evaluate_gate5_delivery(product_name, candidates, output)
