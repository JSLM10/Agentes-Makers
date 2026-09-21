"""
demo.py — Corre un caso de FluentDrop y muestra la traza completa
==================================================================

Pensado para demostrar el agente en vivo: imprime qué decidió cada uno de los
6 Gates, qué motor produjo la respuesta y el contrato final de 9 campos.

Uso:
    python demo.py                      # Caso de ejemplo incluido, contra el modelo real
    python demo.py --offline            # Sin red: motor determinista (a prueba de wifi)
    python demo.py --archivo caso.txt   # Un caso propio desde un archivo
    python demo.py "Producto en tienda: ..."   # Un caso propio en la linea de comandos

Formato de entrada esperado:

    Producto en tienda: <nombre>
    Proveedor actual: <nombre> - $<precio> USD, envio <dias> dias, calificacion <n>/5, stock <n> unidades
    Proveedores candidatos encontrados:
    - <Proveedor X>: $<precio> USD, envio <dias> dias, calificacion <n>/5, stock <n> unidades, url <enlace>

El enlace es opcional. Si viene, el pipeline lo copia al campo `url_producto`
del proveedor que resulte recomendado; el modelo nunca lo escribe.

Autor: Juan Pablo Muñoz
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.pipeline_orchestrator import FluentDropPipeline  # noqa: E402
from src.services.llm_synthesis import LLMSynthesisService  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


CASO_EJEMPLO = """Producto en tienda: Audifonos inalambricos Bluetooth 5.3 con cancelacion de ruido
Categoria: Electronica > Audio
Proveedor actual: Proveedor A - $8.50 USD, envio 15-20 dias, calificacion 3.9/5, stock 12 unidades
Proveedores candidatos encontrados:
- Proveedor B: $7.20 USD, envio 8-12 dias, calificacion 4.6/5, stock 340 unidades, url https://catalogo.example.com/p/audifonos-bt53-proveedor-b
- Proveedor C: $6.90 USD, envio 25-30 dias, calificacion 3.2/5, stock 5 unidades, url https://catalogo.example.com/p/audifonos-bt53-proveedor-c
- Proveedor D: $7.80 USD, envio 10-14 dias, calificacion 4.8/5, stock 200 unidades, url https://catalogo.example.com/p/audifonos-bt53-proveedor-d"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Corre un caso de FluentDrop mostrando la traza de Gates")
    parser.add_argument("texto", nargs="?", help="El caso a evaluar, como texto")
    parser.add_argument("--archivo", help="Lee el caso desde un archivo de texto")
    parser.add_argument("--offline", action="store_true",
                        help="Usa el motor determinista sin tocar la red")
    args = parser.parse_args()

    if args.archivo:
        entrada = Path(args.archivo).read_text(encoding="utf-8")
    elif args.texto:
        entrada = args.texto
    else:
        entrada = CASO_EJEMPLO

    servicio = LLMSynthesisService(offline=True) if args.offline else LLMSynthesisService()

    ancho = 78
    print("=" * ancho)
    print("ENTRADA")
    print("=" * ancho)
    print(entrada.strip())

    print()
    print("=" * ancho)
    print("GATES  (en vivo, a medida que cada aduana resuelve)")
    print("=" * ancho)

    inicio = time.perf_counter()

    def mostrar_gate(gate) -> None:
        """Imprime cada gate apenas termina, sin esperar al resto del pipeline."""
        transcurrido = (time.perf_counter() - inicio) * 1000.0
        marca = "OK  " if gate.passed else "STOP"
        print(f"  [{marca}] {gate.gate_id}  {gate.gate_name}   (+{transcurrido:.1f} ms)")
        print(f"          {gate.reason}")
        sys.stdout.flush()

    reporte = FluentDropPipeline(llm_service=servicio).run(entrada, on_gate=mostrar_gate)

    print()
    print(f"  ejecucion {reporte.execution_id}   total {reporte.latency_ms} ms   motor: {reporte.model_used}")

    salida = reporte.output.model_dump()
    print()
    print("=" * ancho)
    print(f"CONTRATO DE SALIDA ({len(salida)} campos)")
    print("=" * ancho)
    print(json.dumps(salida, ensure_ascii=False, indent=2))

    print()
    print("=" * ancho)
    if salida["proveedor_recomendado"]:
        print(f"  Proveedor recomendado : {salida['proveedor_recomendado']}")
        print(f"  Precio                : ${salida['precio']}")
        print(f"  Enlace al producto    : {salida['url_producto'] or 'no venia en el input'}")
    else:
        print("  Sin recomendacion: ningun candidato supero los filtros.")
    print(f"  Requiere revision humana: {salida['requiere_revision_humana']}")
    print("=" * ancho)


if __name__ == "__main__":
    main()
