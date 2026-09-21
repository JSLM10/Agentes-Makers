"""
test_scoring.py — Pruebas de calibración matemática y monotonicidad del motor MCDA
==================================================================================

Verifica que el motor determinista de scoring cumpla con:
- Monotonicidad de precio: a menor precio relativo, mayor puntuación.
- Penalización no lineal de envíos: envíos rápidos (<= 7 días) obtienen puntaje máximo.
- Umbral de reputación: calificaciones < 3.5 obtienen score 0.0 y se marcan inviables.
- Detección de reseñas críticas: penalización del 60% ante fraudes o roturas.
- Ranking multivariable: desempates consistentes y robustos.
"""

import unittest
from src.schemas.contracts import SupplierCandidate
from src.scoring.mcda_engine import (
    MCDAScoringEngine,
    normalize_shipping_days,
    detect_review_risk,
)


class TestMCDAScoringEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MCDAScoringEngine()

    def test_normalize_shipping_days(self):
        self.assertEqual(normalize_shipping_days("8-12 días"), 10.0)
        self.assertEqual(normalize_shipping_days("15-20 dias"), 17.5)
        self.assertEqual(normalize_shipping_days("2 días"), 2.0)
        self.assertEqual(normalize_shipping_days("5 dias"), 5.0)
        self.assertIsNone(normalize_shipping_days(None))

    def test_price_monotonicity(self):
        ref_price = 10.0
        # Ahorro del 50%, 20%, 0%, y sobrecosto de 30%
        s1 = self.engine.score_price(5.0, ref_price)   # 50% ahorro -> 1.0
        s2 = self.engine.score_price(8.0, ref_price)   # 20% ahorro -> 0.75
        s3 = self.engine.score_price(10.0, ref_price)  # 0% ahorro -> 0.50
        s4 = self.engine.score_price(13.0, ref_price)  # 30% sobrecosto -> 0.0

        self.assertGreaterEqual(s1, s2)
        self.assertGreater(s2, s3)
        self.assertGreater(s3, s4)
        self.assertEqual(s1, 1.0)
        self.assertEqual(s4, 0.0)

    def test_shipping_score_decay(self):
        s_fast = self.engine.score_shipping(5.0)    # <= 7 dias -> 1.0
        s_mid = self.engine.score_shipping(16.0)    # entre 7 y 25
        s_slow = self.engine.score_shipping(35.0)   # > 25 dias -> 0.05

        self.assertEqual(s_fast, 1.0)
        self.assertGreater(s_fast, s_mid)
        self.assertGreater(s_mid, s_slow)
        self.assertEqual(s_slow, 0.05)

    def test_rating_threshold(self):
        s_below = self.engine.score_rating(3.2)  # < 3.5 -> 0.0
        s_base = self.engine.score_rating(3.5)   # == 3.5 -> 0.0
        s_good = self.engine.score_rating(4.5)   # > 3.5 -> > 0
        s_top = self.engine.score_rating(5.0)    # 5.0 -> 1.0

        self.assertEqual(s_below, 0.0)
        self.assertEqual(s_base, 0.0)
        self.assertGreater(s_good, s_base)
        self.assertEqual(s_top, 1.0)

    def test_review_risk_detection(self):
        has_risk, penalty, reason = detect_review_risk("Proveedor excelente, entrega a tiempo")
        self.assertFalse(has_risk)
        self.assertEqual(penalty, 0.0)

        has_risk_scam, penalty_scam, reason_scam = detect_review_risk("Cientos de reportes de que el paquete nunca llegó, es una estafa")
        self.assertTrue(has_risk_scam)
        self.assertEqual(penalty_scam, 0.60)
        self.assertIn("alerta", reason_scam.lower())

    def test_candidate_ranking_with_tradeoffs(self):
        # Escenario similar al Caso Normal del notebook
        ref_price = 8.50
        candidates = [
            SupplierCandidate(nombre="Prov B", precio=7.20, tiempo_envio_estimado="8-12 días", calificacion=4.6, stock_disponible=340),
            SupplierCandidate(nombre="Prov C", precio=6.90, tiempo_envio_estimado="25-30 días", calificacion=3.2, stock_disponible=5),
            SupplierCandidate(nombre="Prov D", precio=7.80, tiempo_envio_estimado="10-14 días", calificacion=4.8, stock_disponible=200),
        ]
        ranked = self.engine.rank_candidates(candidates, ref_price=ref_price)
        
        # Prov C debe quedar al final porque calificacion 3.2 < 3.5 (inviable)
        self.assertEqual(ranked[-1].candidate.nombre, "Prov C")
        self.assertFalse(ranked[-1].candidate.es_viable)
        
        # Prov B debe quedar en 1er lugar (excelente precio + buen envio + alta calificacion)
        self.assertEqual(ranked[0].candidate.nombre, "Prov B")
        self.assertEqual(ranked[0].rank, 1)


if __name__ == "__main__":
    unittest.main()
