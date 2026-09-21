"""
test_url_contract.py — Pruebas del enlace al producto en el contrato
====================================================================

El enlace al producto es el único campo del contrato que el modelo NO produce:
lo copia el pipeline desde el candidato ganador del input. Estas pruebas fijan
esa garantía, porque es lo que hace imposible que el enlace sea alucinado.

Cubre:
- El parser extrae la URL de cada candidato del texto de entrada.
- El pipeline copia al output la URL del proveedor recomendado, no otra.
- Las alternativas llevan su propia URL.
- Un candidato sin URL produce `url_producto` null sin forzar revisión humana
  (un enlace faltante es incómodo, no riesgoso).
- Gate 4 veta si la URL de la salida no coincide con la del input.
- Gate 4 veta si el modelo recomienda un proveedor que no estaba en el input.
"""

import unittest

from src.gates.gates_engine import evaluate_gate4_llm_guardrail
from src.pipeline_orchestrator import FluentDropPipeline, parse_raw_text_input
from src.schemas.contracts import REQUIRED_FIELDS, SupplierCandidate
from src.scoring.mcda_engine import MCDAScoringEngine
from src.services.llm_synthesis import LLMSynthesisService

URL_B = "https://es.aliexpress.com/item/1005006123456.html"
URL_D = "https://www.cjdropshipping.com/product/abc-987"

ENTRADA_CON_URLS = f"""Producto en tienda: Audifonos inalambricos Bluetooth 5.3
Proveedor actual: Proveedor A - $8.50 USD, envio 15-20 dias, calificacion 3.9/5, stock 12 unidades
Proveedores candidatos encontrados:
- Proveedor B: $7.20 USD, envio 8-12 dias, calificacion 4.6/5, stock 340 unidades, url {URL_B}
- Proveedor D: $7.80 USD, envio 10-14 dias, calificacion 4.8/5, stock 200 unidades, url {URL_D}
"""


class TestParserURL(unittest.TestCase):

    def test_parser_extrae_url_de_cada_candidato(self):
        entrada = parse_raw_text_input(ENTRADA_CON_URLS)
        urls = {c.nombre: c.url for c in entrada.proveedores_candidatos}
        self.assertEqual(urls.get("Proveedor B"), URL_B)
        self.assertEqual(urls.get("Proveedor D"), URL_D)

    def test_candidato_sin_url_queda_en_none(self):
        entrada = parse_raw_text_input(
            "Producto en tienda: Mouse\n- Proveedor Sin: $5.00 USD, envio 5 dias, calificacion 4.5/5, stock 50 unidades"
        )
        self.assertIsNone(entrada.proveedores_candidatos[0].url)

    def test_el_proveedor_actual_no_entra_como_candidato(self):
        # La linea "Proveedor actual: ..." matchea el patron de candidato.
        # Sin filtro, el proveedor vigente compite consigo mismo.
        entrada = parse_raw_text_input(ENTRADA_CON_URLS)
        nombres = [c.nombre.strip().lower() for c in entrada.proveedores_candidatos]
        self.assertNotIn("proveedor actual", nombres)
        self.assertEqual(len(entrada.proveedores_candidatos), 2)

    def test_url_no_se_confunde_con_el_precio_ni_el_envio(self):
        entrada = parse_raw_text_input(ENTRADA_CON_URLS)
        b = next(c for c in entrada.proveedores_candidatos if c.nombre == "Proveedor B")
        self.assertEqual(b.precio, 7.20)
        self.assertEqual(b.tiempo_envio_estimado, "8-12 dias")
        self.assertEqual(b.stock_disponible, 340)


class TestContratoURL(unittest.TestCase):

    def test_url_producto_es_parte_del_contrato(self):
        self.assertIn("url_producto", REQUIRED_FIELDS)

    def test_pipeline_copia_la_url_del_proveedor_recomendado(self):
        pipeline = FluentDropPipeline(llm_service=LLMSynthesisService(offline=True))
        reporte = pipeline.run(ENTRADA_CON_URLS)
        salida = reporte.output
        self.assertEqual(salida.proveedor_recomendado, "Proveedor B")
        self.assertEqual(salida.url_producto, URL_B)

    def test_las_alternativas_llevan_su_propia_url(self):
        pipeline = FluentDropPipeline(llm_service=LLMSynthesisService(offline=True))
        reporte = pipeline.run(ENTRADA_CON_URLS)
        urls_alternativas = {a.nombre: a.url for a in reporte.output.alternativas}
        self.assertEqual(urls_alternativas.get("Proveedor D"), URL_D)

    def test_url_faltante_no_fuerza_revision_humana(self):
        # Mismo caso normal pero sin URLs: debe recomendar igual y no pedir revision.
        sin_urls = ENTRADA_CON_URLS.replace(f", url {URL_B}", "").replace(f", url {URL_D}", "")
        pipeline = FluentDropPipeline(llm_service=LLMSynthesisService(offline=True))
        reporte = pipeline.run(sin_urls)
        self.assertEqual(reporte.output.proveedor_recomendado, "Proveedor B")
        self.assertIsNone(reporte.output.url_producto)
        self.assertFalse(reporte.output.requiere_revision_humana)


class TestGate4VetoURL(unittest.TestCase):

    def setUp(self):
        self.candidatos = [
            SupplierCandidate(
                nombre="Proveedor B", precio=7.20, tiempo_envio_estimado="8-12 dias",
                calificacion=4.6, stock_disponible=340, url=URL_B,
            ),
        ]
        self.ranking = MCDAScoringEngine().rank_candidates(self.candidatos, ref_price=8.50)
        self.salida_base = {
            "producto": "Audifonos",
            "proveedor_recomendado": "Proveedor B",
            "precio": 7.20,
            "tiempo_envio_estimado": "8-12 dias",
            "calificacion": 4.6,
            "stock_disponible": 340,
            "alternativas": [],
            "requiere_revision_humana": False,
            "url_producto": URL_B,
        }

    def test_gate4_acepta_url_que_coincide_con_el_input(self):
        g4, salida = evaluate_gate4_llm_guardrail(self.salida_base, self.ranking)
        self.assertTrue(g4.passed, g4.reason)
        self.assertEqual(salida.url_producto, URL_B)

    def test_gate4_veta_url_inventada_y_restaura_la_autoritativa(self):
        falsa = dict(self.salida_base, url_producto="https://sitio-inventado.com/item/999")
        g4, salida = evaluate_gate4_llm_guardrail(falsa, self.ranking)
        self.assertFalse(g4.passed)
        self.assertIn("url", g4.reason.lower())
        # La recomendacion sobrevive, pero la URL se reemplaza por la del input
        self.assertEqual(salida.proveedor_recomendado, "Proveedor B")
        self.assertEqual(salida.url_producto, URL_B)
        self.assertTrue(salida.requiere_revision_humana)

    def test_gate4_veta_proveedor_que_no_estaba_en_el_input(self):
        fantasma = dict(self.salida_base, proveedor_recomendado="Proveedor Fantasma", url_producto=None)
        g4, salida = evaluate_gate4_llm_guardrail(fantasma, self.ranking)
        self.assertFalse(g4.passed)
        self.assertIsNone(salida.proveedor_recomendado)
        self.assertTrue(salida.requiere_revision_humana)

    def test_gate4_sin_ranking_no_valida_url(self):
        # Compatibilidad: sin scored_candidates, Gate 4 no puede verificar la URL.
        g4, salida = evaluate_gate4_llm_guardrail(self.salida_base)
        self.assertTrue(g4.passed, g4.reason)


if __name__ == "__main__":
    unittest.main()
