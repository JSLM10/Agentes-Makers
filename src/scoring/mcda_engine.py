"""
mcda_engine.py — Motor Determinista de Decisión Multicriterio (MCDA)
===================================================================

Calcula puntuaciones deterministas normalizadas para proveedores candidatos de dropshipping
basándose en:
- Ahorro de precio relativo frente al proveedor actual (o benchmark del grupo).
- SLA y velocidad del tiempo de envío (función de penalización no lineal).
- Calificación y reputación (umbral estricto >= 3.5).
- Buffer de seguridad de stock (curva logarítmica).
- Detección heurística de riesgos en reseñas textuales (penalización severa).
"""

import math
import re
from typing import List, Optional, Tuple, Dict
from dataclasses import dataclass, field
from ..schemas.contracts import SupplierCandidate, MIN_CALIFICACION_RECOMENDADA


# Palabras clave y frases de alto riesgo en reseñas de e-commerce
CRITICAL_RISK_PATTERNS = [
    r"nunca\s+lleg[oó]",
    r"no\s+lleg[oó]",
    r"estafa",
    r"scam",
    r"producto\s+roto",
    r"defectuoso",
    r"falso\s+tracking",
    r"falsificaci[oó]n",
    r"p[eé]sima\s+calidad",
    r"no\s+responde",
    r"paquete\s+vac[ií]o",
    r"tard[oó]\s+meses",
]


@dataclass
class ScoredCandidate:
    candidate: SupplierCandidate
    score_precio: float = 0.0
    score_envio: float = 0.0
    score_rating: float = 0.0
    score_stock: float = 0.0
    score_mcda_base: float = 0.0
    penalizacion_riesgo: float = 0.0
    score_final: float = 0.0
    tiene_riesgo_critico: bool = False
    motivo_riesgo: Optional[str] = None
    rank: int = 0


def normalize_shipping_days(envio: Optional[str]) -> Optional[float]:
    """
    Extrae el número promedio de días a partir de un texto o rango.
    Ej: '8-12 días' -> 10.0, '15-20 dias' -> 17.5, '2 días' -> 2.0
    """
    if not envio:
        return None
    
    text = str(envio).lower()
    # Buscar rangos como "8-12" o "8 a 12"
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|a|al)\s*(\d+(?:\.\d+)?)", text)
    if range_match:
        val1 = float(range_match.group(1))
        val2 = float(range_match.group(2))
        return round((val1 + val2) / 2.0, 1)
    
    # Buscar un solo número
    single_match = re.search(r"(\d+(?:\.\d+)?)", text)
    if single_match:
        return float(single_match.group(1))
    
    return None


def detect_review_risk(resenas: Optional[str]) -> Tuple[bool, float, Optional[str]]:
    """
    Analiza reseñas en busca de alertas rojas operativas.
    Retorna: (tiene_riesgo, factor_penalizacion, motivo)
    """
    if not resenas:
        return False, 0.0, None
    
    text = resenas.lower()
    for pattern in CRITICAL_RISK_PATTERNS:
        match = re.search(pattern, text)
        if match:
            return True, 0.60, f"Alerta en reseñas detectada: '{match.group(0)}'"
            
    return False, 0.0, None


class MCDAScoringEngine:
    """
    Motor matemático multicriterio determinista para ranking de proveedores.
    """
    def __init__(
        self,
        weight_price: float = 0.35,
        weight_shipping: float = 0.30,
        weight_rating: float = 0.25,
        weight_stock: float = 0.10,
        min_rating: float = MIN_CALIFICACION_RECOMENDADA,
    ):
        self.wp = weight_price
        self.we = weight_shipping
        self.wr = weight_rating
        self.ws = weight_stock
        self.min_rating = min_rating
        assert math.isclose(self.wp + self.we + self.wr + self.ws, 1.0, rel_tol=1e-3), "Los pesos deben sumar 1.0"

    def score_price(self, price: Optional[float], ref_price: Optional[float]) -> float:
        """
        Función continua de evaluación de precio:
        Delta = (P_ref - P) / P_ref
        Delta >= 0.40 -> 1.0
        Delta <= -0.20 -> 0.0
        En medio: 0.5 + 1.25 * Delta
        """
        if price is None or price <= 0:
            return 0.0
        if ref_price is None or ref_price <= 0:
            return 0.5  # Si no hay referencia, neutral
        
        delta = (ref_price - price) / ref_price
        if delta >= 0.40:
            return 1.0
        if delta <= -0.20:
            return 0.0
        return round(max(0.0, min(1.0, 0.5 + 1.25 * delta)), 4)

    def score_shipping(self, days: Optional[float]) -> float:
        """
        Penalización no lineal para tiempos de tránsito:
        <= 7 días -> 1.0
        7 < d <= 25 -> 1.0 - (d - 7) / 18
        > 25 días -> 0.05
        """
        if days is None or days <= 0:
            return 0.2  # Desconocido con penalización
        if days <= 7.0:
            return 1.0
        if days <= 25.0:
            return round(1.0 - ((days - 7.0) / 18.0), 4)
        return 0.05

    def score_rating(self, rating: Optional[float]) -> float:
        """
        Escalamiento no lineal para calificación [3.5, 5.0]:
        < 3.5 -> 0.0 (inviable)
        >= 3.5 -> ((rating - 3.5) / 1.5) ^ 1.2
        """
        if rating is None or rating < self.min_rating:
            return 0.0
        norm = (rating - self.min_rating) / (5.0 - self.min_rating)
        return round(math.pow(max(0.0, min(1.0, norm)), 1.2), 4)

    def score_stock(self, stock: Optional[int]) -> float:
        """
        Transformación logarítmica saturada para stock hasta 500 unidades:
        min(1.0, ln(1 + S) / ln(1 + 500))
        """
        if stock is None or stock <= 0:
            return 0.0
        max_target = 500.0
        score = math.log(1.0 + stock) / math.log(1.0 + max_target)
        return round(min(1.0, max(0.0, score)), 4)

    def evaluate_candidate(
        self,
        candidate: SupplierCandidate,
        ref_price: Optional[float] = None,
    ) -> ScoredCandidate:
        """Calcula el vector de puntuación y el score final ajustado por riesgo."""
        days = candidate.tiempo_envio_dias or normalize_shipping_days(candidate.tiempo_envio_estimado)
        
        s_price = self.score_price(candidate.precio, ref_price)
        s_ship = self.score_shipping(days)
        s_rate = self.score_rating(candidate.calificacion)
        s_stock = self.score_stock(candidate.stock_disponible)
        
        base_score = (
            self.wp * s_price +
            self.we * s_ship +
            self.wr * s_rate +
            self.ws * s_stock
        )
        
        has_risk, penalty, risk_reason = detect_review_risk(candidate.resenas)
        final_score = base_score * (1.0 - penalty)
        
        # Si la calificación es menor a la mínima o precio <= 0, no puede tener puntaje aprobatorio
        if candidate.calificacion is not None and candidate.calificacion < self.min_rating:
            final_score = 0.0
            candidate.es_viable = False
            candidate.motivo_exclusion = f"Calificación ({candidate.calificacion}) inferior a {self.min_rating}"
            
        return ScoredCandidate(
            candidate=candidate,
            score_precio=s_price,
            score_envio=s_ship,
            score_rating=s_rate,
            score_stock=s_stock,
            score_mcda_base=round(base_score, 4),
            penalizacion_riesgo=penalty,
            score_final=round(final_score, 4),
            tiene_riesgo_critico=has_risk,
            motivo_riesgo=risk_reason,
        )

    def rank_candidates(
        self,
        candidates: List[SupplierCandidate],
        ref_price: Optional[float] = None,
    ) -> List[ScoredCandidate]:
        """Ordena y rankea los candidatos de mejor a peor según su score final determinista."""
        if not candidates:
            return []
        
        # Si no hay ref_price del proveedor actual, usar el promedio o máx de los candidatos
        if ref_price is None or ref_price <= 0:
            precios = [c.precio for c in candidates if c.precio and c.precio > 0]
            ref_price = max(precios) if precios else None
            
        scored = [self.evaluate_candidate(c, ref_price) for c in candidates]
        # Ordenar descendente por score_final, desempatando por calificación y menor precio
        scored.sort(
            key=lambda x: (
                x.score_final,
                x.candidate.calificacion or 0.0,
                -(x.candidate.precio or 999999.0),
            ),
            reverse=True,
        )
        
        for idx, item in enumerate(scored, start=1):
            item.rank = idx
            
        return scored
