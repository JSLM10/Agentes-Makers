"""
_comun.py — Piezas compartidas por las funciones serverless
============================================================

Vercel enruta cada archivo de `api/` como un endpoint, salvo los que empiezan
por guion bajo. Este queda fuera del enrutado y guarda lo que las tres
funciones necesitan: encontrar el paquete `src/`, responder JSON y atender la
verificación previa de CORS.

El `sys.path` hay que tocarlo a mano porque en Vercel la función se ejecuta
desde `api/`, y `src/` vive un nivel más arriba. Llega al paquete de despliegue
gracias a `includeFiles` en `vercel.json`.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# El frontend se sirve del mismo dominio que la API, así que no hace falta
# abrir CORS. Se deja configurable para poder apuntar un frontend local contra
# un backend desplegado mientras se desarrolla.
ORIGEN_PERMITIDO = os.getenv("CORS_ORIGEN", "")

CUERPO_MAXIMO = 256 * 1024


class BaseJSON(BaseHTTPRequestHandler):
    """Respuestas JSON y manejo uniforme de errores."""

    def responder(self, datos, estado: int = 200) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(estado)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        if ORIGEN_PERMITIDO:
            self.send_header("Access-Control-Allow-Origin", ORIGEN_PERMITIDO)
        self.end_headers()
        self.wfile.write(cuerpo)

    def fallar(self, mensaje: str, estado: int = 400) -> None:
        self.responder({"error": mensaje}, estado)

    def leer_json(self):
        """
        El cuerpo de la petición, o None si no es JSON utilizable.

        El tope de tamaño no es ceremonia: sin él, un cuerpo enorme se lee
        entero en memoria antes de descubrir que no servía.
        """
        try:
            largo = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if largo <= 0 or largo > CUERPO_MAXIMO:
            return None
        try:
            return json.loads(self.rfile.read(largo).decode("utf-8"))
        except Exception:
            return None

    def do_OPTIONS(self) -> None:  # noqa: N802 (lo exige BaseHTTPRequestHandler)
        self.send_response(204)
        if ORIGEN_PERMITIDO:
            self.send_header("Access-Control-Allow-Origin", ORIGEN_PERMITIDO)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, formato, *args):
        """Silencia el log por petición: Vercel ya registra cada invocación."""
        return
