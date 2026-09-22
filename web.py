"""
web.py — Interfaz web de FluentDrop
====================================

Sirve una interfaz de despacho que corre el pipeline real y emite la traza de
los 6 Gates en vivo, a medida que cada aduana resuelve.

No agrega dependencias: usa `http.server` de la librería estándar. Se levanta con

    python web.py                 # http://localhost:8000
    python web.py --puerto 9000

La interfaz permite elegir entre el modelo real de NVIDIA NIM y el motor
determinista sin red, que es el modo seguro para demostrar en vivo.

Endpoints:
    GET  /                 Interfaz
    GET  /app.css, /app.js Estáticos
    GET  /api/casos        Los casos de demostración de `casos/`, para que la
                           interfaz los ofrezca sin tener que pegarlos a mano
    POST /api/despachar    Corre un caso y devuelve NDJSON en streaming:
                           una línea por gate apenas resuelve, luego una
                           línea final con el contrato de salida.

Autor: Juan Pablo Muñoz
"""

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
CASOS_DIR = ROOT / "casos"
sys.path.insert(0, str(ROOT))

from src.pipeline_orchestrator import FluentDropPipeline  # noqa: E402
from src.services.llm_synthesis import LLMSynthesisService  # noqa: E402

TIPOS = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}

# Etiquetas en lenguaje de despacho para cada aduana del pipeline.
NOMBRES_GATE = {
    "G0": "Pre-vuelo y credenciales",
    "G1": "Integridad del manifiesto",
    "G2": "Filtros duros de negocio",
    "G3": "Triaje de viabilidad",
    "G4": "Contrato y guardarraíles",
    "G5": "Despacho e idempotencia",
}


def cargar_casos():
    """
    Lee el índice de casos y el texto de cada uno.

    Los .txt son la única fuente: los mismos archivos que consume
    `demo.py --archivo`. Un caso cuyo archivo falte se omite en vez de romper
    la interfaz, para que una demo a medio armar siga levantando.
    """
    indice = CASOS_DIR / "casos.json"
    if not indice.is_file():
        return []

    casos = []
    for entrada in json.loads(indice.read_text(encoding="utf-8")):
        archivo = CASOS_DIR / entrada["archivo"]
        if not archivo.is_file():
            continue
        casos.append({
            "id": entrada["id"],
            "titulo": entrada["titulo"],
            "nota": entrada["nota"],
            "texto": archivo.read_text(encoding="utf-8").strip(),
        })
    return casos


class Manejador(BaseHTTPRequestHandler):
    # HTTP/1.0 permite cerrar la conexión para señalar el fin del stream,
    # sin necesidad de codificación chunked.
    protocol_version = "HTTP/1.0"
    server_version = "FluentDrop"

    def log_message(self, formato, *args):
        """Silencia el log por petición; el servidor ya anuncia su arranque."""

    # --- GET: interfaz, estáticos y casos de demostración ---
    def do_GET(self):
        ruta = self.path.split("?")[0]

        if ruta == "/api/casos":
            cuerpo = json.dumps(cargar_casos(), ensure_ascii=False).encode("utf-8")
            self._enviar(200, "application/json; charset=utf-8", cuerpo)
            return

        nombre = "index.html" if ruta == "/" else ruta.lstrip("/")
        archivo = (WEB_DIR / nombre).resolve()

        if WEB_DIR not in archivo.parents or not archivo.is_file():
            self._enviar(404, "text/plain; charset=utf-8", b"No existe")
            return

        self._enviar(
            200,
            TIPOS.get(archivo.suffix, "application/octet-stream"),
            archivo.read_bytes(),
        )

    # --- POST: corre el pipeline y emite la traza en vivo ---
    def do_POST(self):
        if self.path.split("?")[0] != "/api/despachar":
            self._enviar(404, "text/plain; charset=utf-8", b"No existe")
            return

        try:
            largo = int(self.headers.get("Content-Length") or 0)
            peticion = json.loads(self.rfile.read(largo) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._enviar(400, "application/json", b'{"error":"Cuerpo invalido"}')
            return

        entrada = (peticion.get("entrada") or "").strip()
        if not entrada:
            self._enviar(400, "application/json", b'{"error":"Falta el manifiesto"}')
            return

        offline = bool(peticion.get("offline"))

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        inicio = time.perf_counter()

        def emitir(objeto):
            """Escribe una línea NDJSON y la empuja al cliente de inmediato."""
            self.wfile.write((json.dumps(objeto, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()

        def al_resolver_gate(gate):
            emitir({
                "tipo": "gate",
                "id": gate.gate_id,
                "nombre": NOMBRES_GATE.get(gate.gate_id, gate.gate_name),
                "aprobado": gate.passed,
                "critico": gate.critical,
                "motivo": gate.reason,
                "ms": round((time.perf_counter() - inicio) * 1000.0, 1),
            })

        try:
            servicio = LLMSynthesisService(offline=True) if offline else LLMSynthesisService()
            reporte = FluentDropPipeline(llm_service=servicio).run(entrada, on_gate=al_resolver_gate)
            emitir({
                "tipo": "resultado",
                "ejecucion": reporte.execution_id,
                "motor": reporte.model_used,
                "ms": reporte.latency_ms,
                "gates_criticos_ok": reporte.all_gates_passed,
                "salida": reporte.output.model_dump(),
            })
        except Exception as exc:  # El front necesita saber qué falló, no un 500 mudo.
            emitir({"tipo": "error", "mensaje": f"{type(exc).__name__}: {exc}"})

    def _enviar(self, codigo, tipo, cuerpo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)


def main():
    parser = argparse.ArgumentParser(description="Interfaz web de FluentDrop")
    parser.add_argument("--puerto", type=int, default=8000)
    args = parser.parse_args()

    servidor = ThreadingHTTPServer(("127.0.0.1", args.puerto), Manejador)
    print(f"FluentDrop en http://localhost:{args.puerto}  (Ctrl+C para detener)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nDetenido.")
        servidor.server_close()


if __name__ == "__main__":
    main()
