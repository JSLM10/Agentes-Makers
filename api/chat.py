"""
chat.py — POST /api/chat
=========================

El endpoint del agente. Recibe la conversación completa y devuelve el turno:
la respuesta, la traza de aduanas, el contrato validado y la evidencia MCDA.

No guarda estado. El cliente manda el historial entero en cada petición, que
es lo que corresponde a una función serverless: sin sesión en memoria, dos
peticiones consecutivas pueden caer en instancias distintas y la conversación
se partiría por la mitad.

Petición:
    {"mensajes": [{"rol": "usuario", "contenido": "..."}, ...],
     "offline": false}

Respuesta: ver `Turno.a_dict()` en src/conversacion/agente.py
"""

from _comun import BaseJSON  # noqa: E402  (ajusta sys.path al importarse)

from src.conversacion import AgenteConversacional  # noqa: E402

MAX_MENSAJES = 40
MAX_CARACTERES = 4000

# El agente se construye una vez por instancia, no por petición: así el
# cliente HTTP se reaprovecha entre invocaciones calientes.
_agentes = {}


def obtener_agente(offline: bool) -> AgenteConversacional:
    if offline not in _agentes:
        _agentes[offline] = AgenteConversacional(offline=offline)
    return _agentes[offline]


def limpiar(mensajes):
    """
    Deja solo mensajes con la forma esperada.

    El rol se normaliza a 'usuario' o 'agente' en vez de confiar en lo que
    venga: de él depende qué texto se usa para anclar las cifras, así que un
    rol arbitrario del cliente podría colar como "dicho por el usuario" algo
    que el usuario nunca escribió.
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


class handler(BaseJSON):  # noqa: N801 (Vercel espera este nombre exacto)
    def do_POST(self) -> None:  # noqa: N802
        datos = self.leer_json()
        if datos is None:
            self.fallar("El cuerpo de la petición debe ser JSON válido.")
            return

        mensajes = limpiar(datos.get("mensajes") or [])
        if not mensajes:
            self.fallar("Hace falta al menos un mensaje con contenido.")
            return
        if mensajes[-1]["rol"] != "usuario":
            self.fallar("El último mensaje debe ser del usuario.")
            return

        agente = obtener_agente(bool(datos.get("offline")))

        try:
            turno = agente.responder(mensajes)
        except Exception as exc:
            # El pipeline degrada solo ante fallos del modelo. Si algo escapa
            # hasta aquí es un error del servidor, y se dice como tal en vez
            # de devolver una respuesta vacía que parezca del agente.
            self.fallar(f"Error interno del agente: {type(exc).__name__}", 500)
            return

        self.responder(turno.a_dict())

    def do_GET(self) -> None:  # noqa: N802
        self.fallar("Este endpoint solo acepta POST.", 405)
