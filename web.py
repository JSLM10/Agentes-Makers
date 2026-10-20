"""
web.py — Servidor local de FluentDrop
======================================

Espejo del despliegue en Vercel, para desarrollar y para demostrar sin red.
Sirve el mismo frontend de `public/` y los mismos tres endpoints que las
funciones de `api/`, con los mismos nombres y las mismas respuestas:

    GET  /                  Interfaz
    GET  /app.css, /app.js  Estáticos
    GET  /api/salud         Modelo configurado y si hay credenciales
    GET  /api/casos         Casos de prueba de `casos/`, con su contenido
    POST /api/chat          Un turno del agente

Que local y producción compartan rutas no es cosmético: evita que la demo
funcione en el portátil y se caiga en la URL pública por una diferencia de
contrato que nadie probó.

Se levanta con:

    python web.py                 # http://localhost:8000
    python web.py --puerto 9000
    python web.py --offline       # fuerza el motor determinista

No agrega dependencias: usa `http.server` de la librería estándar.

Autor: Juan Pablo Muñoz
"""

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
PUBLICO = RAIZ / "public"
CASOS_DIR = RAIZ / "casos"
sys.path.insert(0, str(RAIZ))

from src.conversacion import AgenteConversacional  # noqa: E402
from src.services.llm_synthesis import DEFAULT_MODEL, NVIDIA_BASE_URL  # noqa: E402

TIPOS = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}

MAX_MENSAJES = 40
MAX_CARACTERES = 4000

# Lo fija el arranque con --offline y lo lee el manejador.
FORZAR_OFFLINE = False
_agentes = {}


def obtener_agente(offline: bool) -> AgenteConversacional:
    """Un agente por modo, reutilizado entre peticiones."""
    if offline not in _agentes:
        _agentes[offline] = AgenteConversacional(offline=offline)
    return _agentes[offline]


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
        casos.append(
            {
                "id": entrada["id"],
                "titulo": entrada["titulo"],
                "nota": entrada["nota"],
                "contenido": archivo.read_text(encoding="utf-8").strip(),
            }
        )
    return casos


def limpiar(mensajes):
    """
    Deja solo mensajes con la forma esperada, igual que la función de Vercel.

    El rol se normaliza en vez de confiar en lo que venga: de él depende qué
    texto se usa para anclar las cifras del veredicto.
    """
    limpios = []
    for crudo in mensajes[-MAX_MENSAJES:]:
        if not isinstance(crudo, dict):
            continue
        contenido = str(crudo.get("contenido") or "").strip()[:MAX_CARACTERES]
        if not contenido:
            continue
        rol = "usuario" if str(crudo.get("rol")) == "usuario" else "agente"
        limpios.append({"rol": rol, "contenido": contenido})
    return limpios


class Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FluentDrop"

    def log_message(self, formato, *args):
        """Silencia el log por petición; el servidor ya anuncia su arranque."""

    # --- GET: interfaz, estáticos, salud y casos ---
    def do_GET(self):  # noqa: N802
        ruta = self.path.split("?")[0]

        if ruta == "/api/salud":
            import os

            tiene_clave = bool(os.getenv("NVIDIA_API_KEY")) and not FORZAR_OFFLINE
            self._json(
                200,
                {
                    "estado": "ok",
                    "modelo": DEFAULT_MODEL,
                    "endpoint": NVIDIA_BASE_URL,
                    "modelo_disponible": tiene_clave,
                    "motor": "modelo" if tiene_clave else "determinista",
                    "nota": "Servidor local",
                },
            )
            return

        if ruta == "/api/casos":
            self._json(200, cargar_casos())
            return

        if ruta.startswith("/api/"):
            self._json(404, {"error": "No existe ese endpoint."})
            return

        nombre = "index.html" if ruta == "/" else ruta.lstrip("/")
        archivo = (PUBLICO / nombre).resolve()

        if PUBLICO not in archivo.parents or not archivo.is_file():
            self._enviar(404, "text/plain; charset=utf-8", b"No existe")
            return

        self._enviar(
            200,
            TIPOS.get(archivo.suffix, "application/octet-stream"),
            archivo.read_bytes(),
        )

    # --- POST: un turno del agente ---
    def do_POST(self):  # noqa: N802
        if self.path.split("?")[0] != "/api/chat":
            self._json(404, {"error": "No existe ese endpoint."})
            return

        try:
            largo = int(self.headers.get("Content-Length") or 0)
            peticion = json.loads(self.rfile.read(largo) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "El cuerpo de la petición debe ser JSON válido."})
            return

        mensajes = limpiar(peticion.get("mensajes") or [])
        if not mensajes:
            self._json(400, {"error": "Hace falta al menos un mensaje con contenido."})
            return
        if mensajes[-1]["rol"] != "usuario":
            self._json(400, {"error": "El último mensaje debe ser del usuario."})
            return

        offline = FORZAR_OFFLINE or bool(peticion.get("offline"))

        try:
            turno = obtener_agente(offline).responder(mensajes)
        except Exception as exc:  # El front necesita saber qué falló, no un 500 mudo.
            self._json(500, {"error": f"Error interno del agente: {type(exc).__name__}: {exc}"})
            return

        self._json(200, turno.a_dict())

    def _json(self, codigo, datos):
        self._enviar(
            codigo,
            "application/json; charset=utf-8",
            json.dumps(datos, ensure_ascii=False).encode("utf-8"),
        )

    def _enviar(self, codigo, tipo, cuerpo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)


def main():
    global FORZAR_OFFLINE

    parser = argparse.ArgumentParser(description="Servidor local de FluentDrop")
    parser.add_argument("--puerto", type=int, default=8000)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Fuerza el motor determinista: ni la lectura ni la síntesis llaman al modelo.",
    )
    args = parser.parse_args()
    FORZAR_OFFLINE = args.offline

    servidor = ThreadingHTTPServer(("127.0.0.1", args.puerto), Manejador)
    motor = "determinista (--offline)" if args.offline else DEFAULT_MODEL
    print(f"FluentDrop en http://localhost:{args.puerto}")
    print(f"Motor: {motor}")
    print("Ctrl+C para detener.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nDetenido.")
        servidor.server_close()


if __name__ == "__main__":
    main()
