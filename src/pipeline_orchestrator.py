"""
pipeline_orchestrator.py — Orquestador Central del Pipeline de Microservicios
=============================================================================

Coordina la ejecución secuencial gobernada por Gates y servicios desacoplados:
Input Crudo/Estructurado -> G0 (Preflight) -> G1 (Ingestion) -> G2 (Hard Filter) ->
G3 (Triage) -> MCDA Scoring Engine -> LLM Synthesis -> G4 (Guardrail) -> G5 (Delivery)
"""

import json
import time
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple, Union

from .schemas.contracts import (
    ProductInput,
    SupplierCandidate,
    CurrentSupplier,
    FluentDropOutput,
    GateResult,
    PipelineReport,
    MIN_CALIFICACION_RECOMENDADA,
)
from .scoring.mcda_engine import MCDAScoringEngine, ScoredCandidate, normalize_shipping_days
from .gates.gates_engine import GatesEngine
from .services.llm_synthesis import LLMSynthesisService


def parse_raw_text_input(raw_text: str) -> ProductInput:
    """
    Parser heurístico para transformar texto libre en ProductInput estructurado.
    Soporta los formatos utilizados en el notebook y en los casos de evaluación.
    """
    import re
    
    # 1. Nombre del producto
    prod_match = re.search(r"Producto\s*(?:en\s*tienda)?\s*:\s*([^\n\r]+)", raw_text, re.IGNORECASE)
    producto = prod_match.group(1).strip() if prod_match else "Producto Desconocido"
    
    # 2. Categoría
    cat_match = re.search(r"Categor[ií]a\s*:\s*([^\n\r]+)", raw_text, re.IGNORECASE)
    categoria = cat_match.group(1).strip() if cat_match else None
    
    # 3. Proveedor actual
    current_supplier = None
    act_match = re.search(r"Proveedor\s*actual\s*:\s*([^\n\r]+)", raw_text, re.IGNORECASE)
    if act_match:
        act_line = act_match.group(1)
        name_part = act_line.split("-")[0].strip()
        price_m = re.search(r"\$(\d+(?:\.\d+)?)", act_line)
        price_val = float(price_m.group(1)) if price_m else 0.0
        current_supplier = CurrentSupplier(nombre=name_part, precio=price_val)
        
    # 4. Proveedores candidatos
    candidates: List[SupplierCandidate] = []
    
    # Buscar patrones como "- Proveedor B: $7.20 USD, envío 8-12 días, calificación 4.6/5, stock 340 unidades"
    # O "Proveedor Y: envío en 2 días, precio $1.20 USD, calificación 1.4/5..."
    lines = raw_text.splitlines()
    for line in lines:
        line_clean = line.strip()
        cand_match = re.search(r"(?:^-\s*|^)(Proveedor\s+[A-Za-z0-9_]+)\s*:\s*(.*)", line_clean, re.IGNORECASE)
        if cand_match:
            name = cand_match.group(1).strip()
            # Ignorar si es el proveedor actual
            if current_supplier and name.lower() == current_supplier.nombre.lower():
                continue
            
            body = cand_match.group(2)
            
            # Precio
            p_m = re.search(r"\$(\d+(?:\.\d+)?)", body)
            price = float(p_m.group(1)) if p_m else None
            
            # Envío: captura hasta la siguiente coma y exige al menos un dígito, para
            # aceptar tildes ("8-12 días") y descartar texto sin plazo ("envío informado").
            env_m = re.search(r"env[ií]o\s*(?:en\s*)?([^,;]*\d[^,;]*)", body, re.IGNORECASE)
            envio = env_m.group(1).strip() if env_m else None
            
            # Calificación
            cal_m = re.search(r"calificaci[oó]n\s*(\d+(?:\.\d+)?)\s*(?:/\s*5)?", body, re.IGNORECASE)
            calif = float(cal_m.group(1)) if cal_m else None
            
            # Stock
            stk_m = re.search(r"stock\s*(\d+)", body, re.IGNORECASE)
            stock = int(stk_m.group(1)) if stk_m else None
            
            # Reseñas
            res_m = re.search(r"reseñas\s*(.*)", body, re.IGNORECASE)
            resenas = res_m.group(1).strip() if res_m else body
            
            candidates.append(
                SupplierCandidate(
                    nombre=name,
                    precio=price,
                    tiempo_envio_estimado=envio,
                    calificacion=calif,
                    stock_disponible=stock,
                    resenas=resenas,
                )
            )
            
    return ProductInput(
        producto=producto,
        categoria=categoria,
        proveedor_actual=current_supplier,
        proveedores_candidatos=candidates,
    )


class FluentDropPipeline:
    """Orquestador del pipeline completo de FluentDrop."""
    def __init__(
        self,
        gates_engine: Optional[GatesEngine] = None,
        scoring_engine: Optional[MCDAScoringEngine] = None,
        llm_service: Optional[LLMSynthesisService] = None,
    ):
        self.gates = gates_engine or GatesEngine()
        self.scoring = scoring_engine or MCDAScoringEngine()
        self.llm = llm_service or LLMSynthesisService()

    def run(self, input_data: Union[str, ProductInput, Dict[str, Any]]) -> PipelineReport:
        """
        Ejecuta el pipeline completo gobernado por los 6 Gates.
        """
        start_time = time.perf_counter()
        execution_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().isoformat()
        gates_evaluated: List[GateResult] = []

        # Determinar representación textual para prompts y Gate 1
        raw_text = ""
        structured_input: Optional[ProductInput] = None
        
        if isinstance(input_data, str):
            raw_text = input_data
            structured_input = parse_raw_text_input(raw_text)
        elif isinstance(input_data, ProductInput):
            structured_input = input_data
            raw_text = f"Producto: {structured_input.producto}\n"
            for c in structured_input.proveedores_candidatos:
                raw_text += f"- {c.nombre}: ${c.precio}, {c.tiempo_envio_estimado}, calif {c.calificacion}, stock {c.stock_disponible}\n"
        elif isinstance(input_data, dict):
            structured_input = ProductInput(**input_data)
            raw_text = json.dumps(input_data, ensure_ascii=False)
        else:
            raw_text = str(input_data)

        # --- GATE 0: Pre-Flight ---
        g0 = self.gates.run_pre_flight()
        gates_evaluated.append(g0)

        # --- GATE 1: Ingestion Integrity & Sanitizer ---
        g1, requires_review_g1 = self.gates.run_ingestion(raw_text)
        gates_evaluated.append(g1)

        product_name = structured_input.producto if structured_input else "Producto"
        candidates = structured_input.proveedores_candidatos if structured_input else []
        ref_price = structured_input.proveedor_actual.precio if (structured_input and structured_input.proveedor_actual) else None

        # --- GATE 2: Hard Filter ---
        g2, viable_candidates = self.gates.run_hard_filter(candidates)
        gates_evaluated.append(g2)

        # --- GATE 3: Viability Triage ---
        g3, should_invoke_llm = self.gates.run_triage(len(viable_candidates), product_name)
        gates_evaluated.append(g3)

        model_name = None
        output_dict: Dict[str, Any] = {}

        # Evaluación matemática MCDA
        scored_candidates = self.scoring.rank_candidates(candidates, ref_price=ref_price)

        if not should_invoke_llm:
            # Cortocircuito sin coste de LLM
            output_dict = {
                "producto": product_name if (product_name and product_name != "Producto Desconocido") else None,
                "proveedor_recomendado": None,
                "precio": None,
                "tiempo_envio_estimado": None,
                "calificacion": None,
                "stock_disponible": None,
                "alternativas": [],
                "requiere_revision_humana": True,
            }
            model_name = "short_circuit_zero_llm"
        else:
            # Invocación a servicio de síntesis LLM
            output_dict, _, model_name = self.llm.generate_recommendation(
                user_prompt=raw_text,
                product_name=product_name,
                scored_candidates=scored_candidates,
            )

        # Si el Gate 1 detectó inyección o anomalía, forzar flag de revisión humana
        if requires_review_g1:
            output_dict["requiere_revision_humana"] = True

        # --- GATE 4: LLM Guardrail & Contract ---
        g4, validated_output = self.gates.run_guardrails(output_dict, scored_candidates)
        gates_evaluated.append(g4)

        # --- GATE 5: Delivery & Idempotency ---
        g5, hash_id = self.gates.run_delivery(product_name, candidates, validated_output)
        gates_evaluated.append(g5)

        total_latency = round((time.perf_counter() - start_time) * 1000.0, 2)
        all_passed = all(g.passed for g in gates_evaluated if g.critical)

        return PipelineReport(
            execution_id=execution_id,
            timestamp=timestamp,
            product_name=product_name,
            all_gates_passed=all_passed,
            gates_evaluated=gates_evaluated,
            output=validated_output,
            latency_ms=total_latency,
            model_used=model_name,
            source_type="pipeline_orchestrator",
        )
