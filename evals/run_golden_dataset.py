"""
run_golden_dataset.py — Layer 4: Benchmark contra el Golden Dataset
===================================================================

Ejecuta los escenarios curados de `evals/data/golden_dataset.json` a través del
pipeline completo (6 Gates + MCDA + síntesis) y compara la salida real contra la
decisión esperada por un comerciante experto.

El scoring separa seguridad de alineación, porque no todos los desvíos cuestan igual.

Checks BLOQUEANTES (determinan si el escenario aprueba):

1. recomendacion      — el proveedor recomendado coincide con el esperado.
2. proveedor_vetado   — ningún proveedor de `forbidden_recommendations` fue recomendado.
3. flag_revision      — no se omitió `requiere_revision_humana` cuando el escenario
                        la exige (sub-cautela). Pedirla de más NO reprueba.
4. contrato           — esquema de 8 campos, seguridad, nulls y calificación mínima
                        (reutiliza las evals de `eval_fluentdrop.py`).

Métrica INFORMATIVA (no reprueba, mide fricción operativa):

- alineacion_flag     — cuántos casos piden revisión humana de más (sobre-cautela).
                        Un humano revisa algo innecesario: cuesta tiempo, no seguridad.

Modos de ejecución:

    python evals/run_golden_dataset.py           # Offline determinista (sin red, sin API key)
    python evals/run_golden_dataset.py --live     # Contra el modelo real de NVIDIA NIM

El modo offline usa el fallback determinista del servicio de síntesis, por lo que es
reproducible y apto para demo en vivo. El modo --live requiere NVIDIA_API_KEY en .env
y sirve para medir si un modelo concreto respeta las decisiones del benchmark.

El script sale con código 1 si la tasa de casos aprobados cae por debajo de
MIN_PASS_RATE, para poder usarse como gate de CI.

Autor: Juan Pablo Muñoz
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_fluentdrop import run_all_evals  # noqa: E402
from src.pipeline_orchestrator import FluentDropPipeline  # noqa: E402
from src.services.llm_synthesis import LLMSynthesisService  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATASET_PATH = ROOT / "evals" / "data" / "golden_dataset.json"
RESULTS_DIR = ROOT / "evals" / "results"

# Umbral de aprobación del benchmark (documentado en ARCHITECTURE_MICROSERVICIOS.md)
MIN_PASS_RATE = 0.90


def load_dataset() -> list:
    """Carga los escenarios curados del Golden Dataset."""
    if not DATASET_PATH.exists():
        sys.exit(f"No se encontró el dataset en {DATASET_PATH}")
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def build_pipeline(live: bool) -> FluentDropPipeline:
    """
    Construye el pipeline en modo offline determinista o en vivo contra NVIDIA NIM.
    En modo offline se usa offline=True, que impide construir el cliente HTTP aunque
    exista NVIDIA_API_KEY, de modo que el benchmark no dependa de la red.
    """
    if live:
        import os

        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        if not os.getenv("NVIDIA_API_KEY"):
            sys.exit("Modo --live requiere NVIDIA_API_KEY en .env")
        return FluentDropPipeline()

    return FluentDropPipeline(llm_service=LLMSynthesisService(offline=True))


def clasificar_flag(flag_esperado: bool, flag_obtenido: bool) -> str:
    """
    Clasifica la divergencia en `requiere_revision_humana`.

    La asimetría es deliberada: marcar revisión de más sólo cuesta fricción
    (un humano revisa algo que no hacía falta), mientras que NO marcarla cuando
    el escenario la exige deja pasar una decisión insegura sin supervisión.
    """
    if flag_esperado is flag_obtenido:
        return "exacto"
    if flag_obtenido and not flag_esperado:
        return "sobre_cautela"
    return "sub_cautela"


def check_case(case: dict, output: dict) -> list:
    """
    Compara la salida real contra la decisión esperada del comerciante experto.

    Cada check lleva `blocking`: los bloqueantes determinan si el escenario aprueba;
    los no bloqueantes se reportan como métrica de alineación sin reprobar el caso.
    """
    recomendado = output.get("proveedor_recomendado")
    esperado = case.get("expected_recommendation")
    vetados = case.get("forbidden_recommendations") or []
    flag_esperado = case.get("expected_review_required")
    flag_obtenido = output.get("requiere_revision_humana")
    clasificacion = clasificar_flag(flag_esperado, flag_obtenido)

    checks = [
        {
            "test": "recomendacion",
            "blocking": True,
            "pass": recomendado == esperado,
            "esperado": esperado,
            "obtenido": recomendado,
        },
        {
            "test": "proveedor_vetado",
            "blocking": True,
            "pass": recomendado not in vetados,
            "vetados": vetados,
            "obtenido": recomendado,
        },
        {
            # Sólo la sub-cautela reprueba: dejar de pedir supervisión humana
            # cuando el escenario la exige es un fallo de seguridad.
            "test": "flag_revision",
            "blocking": True,
            "pass": clasificacion != "sub_cautela",
            "clasificacion": clasificacion,
            "esperado": flag_esperado,
            "obtenido": flag_obtenido,
        },
    ]

    # Capa de contrato: esquema, seguridad, nulls y calificación mínima
    contrato = run_all_evals(output)
    fallidos = [r["test"] for r in contrato["results"] if not r["pass"]]
    checks.append(
        {
            "test": "contrato",
            "blocking": True,
            "pass": len(fallidos) == 0,
            "sub_tests_fallidos": fallidos,
        }
    )

    # Métrica informativa de fricción: revisión humana pedida de más.
    checks.append(
        {
            "test": "alineacion_flag",
            "blocking": False,
            "pass": clasificacion == "exacto",
            "clasificacion": clasificacion,
        }
    )
    return checks


def run_case(pipeline: FluentDropPipeline, case: dict) -> dict:
    """Ejecuta un escenario del dataset y evalúa su resultado."""
    report = pipeline.run(case["input_text"])
    output = report.output.model_dump()
    checks = check_case(case, output)
    bloqueantes = [c for c in checks if c["blocking"]]
    aprobados = sum(1 for c in bloqueantes if c["pass"])
    flag_check = next(c for c in checks if c["test"] == "alineacion_flag")

    return {
        "id": case["id"],
        "nombre": case["nombre"],
        "categoria": case.get("categoria"),
        "all_pass": aprobados == len(bloqueantes),
        "aprobados": aprobados,
        "total_checks": len(bloqueantes),
        "clasificacion_flag": flag_check["clasificacion"],
        "latencia_ms": report.latency_ms,
        "modelo": report.model_used,
        "gates_criticos_ok": report.all_gates_passed,
        "gates_bloqueados": [
            {"gate": g.gate_id, "motivo": g.reason}
            for g in report.gates_evaluated
            if not g.passed
        ],
        "output": output,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark de FluentDrop contra el Golden Dataset")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Ejecuta contra el modelo real de NVIDIA NIM en vez del fallback determinista",
    )
    args = parser.parse_args()

    dataset = load_dataset()
    pipeline = build_pipeline(args.live)
    modo = "live" if args.live else "offline_determinista"

    print("=" * 72)
    print(f"FluentDrop — Golden Dataset ({len(dataset)} escenarios, modo {modo})")
    print("=" * 72)

    runs = [run_case(pipeline, case) for case in dataset]

    for r in runs:
        estado = "PASS" if r["all_pass"] else "FAIL"
        nota = "" if r["clasificacion_flag"] == "exacto" else f"  [{r['clasificacion_flag']}]"
        print(f"\n[{estado}] {r['id']}: {r['aprobados']}/{r['total_checks']} ({r['latencia_ms']} ms){nota}")
        if not r["all_pass"]:
            print(f"    {r['nombre']}")
            for c in r["checks"]:
                if c["blocking"] and not c["pass"]:
                    detalle = {k: v for k, v in c.items() if k not in ("test", "pass", "blocking")}
                    print(f"    FALLA {c['test']}: {json.dumps(detalle, ensure_ascii=False)}")

    aprobados = sum(1 for r in runs if r["all_pass"])
    pass_rate = aprobados / len(runs) if runs else 0.0
    sobre_cautela = [r["id"] for r in runs if r["clasificacion_flag"] == "sobre_cautela"]
    sub_cautela = [r["id"] for r in runs if r["clasificacion_flag"] == "sub_cautela"]
    latencias = sorted(r["latencia_ms"] for r in runs)
    p95_idx = max(0, int(round(0.95 * len(latencias))) - 1)

    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "modo": modo,
        "escenarios": len(runs),
        "escenarios_pass": aprobados,
        "pass_rate": round(pass_rate, 4),
        "umbral_minimo": MIN_PASS_RATE,
        "cumple_umbral": pass_rate >= MIN_PASS_RATE,
        "alineacion": {
            "exacto": len(runs) - len(sobre_cautela) - len(sub_cautela),
            "sobre_cautela": len(sobre_cautela),
            "sub_cautela": len(sub_cautela),
            "casos_sobre_cautela": sobre_cautela,
            "casos_sub_cautela": sub_cautela,
        },
        "latencia_promedio_ms": round(sum(latencias) / len(latencias), 2) if latencias else 0.0,
        "latencia_p95_ms": latencias[p95_idx] if latencias else 0.0,
        "runs": runs,
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"golden_{modo}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"SEGURIDAD (bloqueante)   {aprobados}/{len(runs)} ({pass_rate:.0%}) | umbral {MIN_PASS_RATE:.0%}")
    print(f"ALINEACION (informativo) exacto {summary['alineacion']['exacto']}/{len(runs)}"
          f" | sobre-cautela {len(sobre_cautela)} | sub-cautela {len(sub_cautela)}")
    if sobre_cautela:
        print(f"  Sobre-cautela (pide revision humana de mas): {', '.join(sobre_cautela)}")
    if sub_cautela:
        print(f"  Sub-cautela (NO pidio revision requerida): {', '.join(sub_cautela)}")
    print(f"Latencia promedio: {summary['latencia_promedio_ms']} ms | p95: {summary['latencia_p95_ms']} ms")
    print(f"Reporte: {out_path.relative_to(ROOT)}")
    print("=" * 72)

    if not summary["cumple_umbral"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
