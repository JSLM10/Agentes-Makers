"""
casos.py — GET /api/casos
==========================

Los casos de prueba adversariales de `casos/`, con su contenido ya incluido,
para que la interfaz los ofrezca con un clic en vez de obligar a pegarlos a
mano durante una sustentación.

Devuelve una lista de {id, titulo, nota, contenido}.
"""

import json

from _comun import RAIZ, BaseJSON  # noqa: E402

CASOS_DIR = RAIZ / "casos"
INDICE = CASOS_DIR / "casos.json"


def cargar_casos():
    """Lee el índice y adjunta a cada entrada el texto de su archivo."""
    if not INDICE.exists():
        return []

    try:
        indice = json.loads(INDICE.read_text(encoding="utf-8"))
    except Exception:
        return []

    casos = []
    for entrada in indice:
        archivo = CASOS_DIR / str(entrada.get("archivo", ""))
        # El nombre viene de un archivo del repositorio, pero se comprueba que
        # el resultado siga dentro de `casos/` antes de leerlo.
        try:
            archivo.resolve().relative_to(CASOS_DIR.resolve())
        except (ValueError, OSError):
            continue
        if not archivo.is_file():
            continue

        casos.append(
            {
                "id": entrada.get("id"),
                "titulo": entrada.get("titulo"),
                "nota": entrada.get("nota"),
                "contenido": archivo.read_text(encoding="utf-8").strip(),
            }
        )
    return casos


class handler(BaseJSON):  # noqa: N801
    def do_GET(self) -> None:  # noqa: N802
        try:
            self.responder(cargar_casos())
        except Exception as exc:
            self.fallar(f"No se pudieron cargar los casos: {type(exc).__name__}", 500)
