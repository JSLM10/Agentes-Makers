"""
redaccion.py — Del contrato validado a una respuesta que se puede leer
=======================================================================

El pipeline entrega un contrato de 9 campos. Un comerciante no quiere leer
JSON, quiere leer una frase. Este módulo escribe esa frase.

La regla que gobierna el módulo entero:

    El veredicto se arma a partir del contrato YA VALIDADO por Gate 4, campo
    por campo, con plantillas deterministas. El modelo no lo escribe.

No es purismo. Gate 4 puede haber vetado una recomendación —calificación bajo
el mínimo, proveedor que no estaba en el input, enlace que no coincide— y
dejado el contrato en null. Si la prosa saliera del modelo, nada impediría que
siguiera recomendando al proveedor vetado con otras palabras: la aduana
quedaría en la traza, pero el usuario leería lo contrario. Redactando desde el
contrato, lo que el usuario lee no puede contradecir lo que los gates
decidieron.

El modelo sí escribe las respuestas que no cargan un veredicto: saludos,
aclaraciones y rechazos fuera de dominio. Ahí no hay cifras que proteger, y un
texto natural hace al agente mucho más usable. Cuando no hay clave de API o la
llamada falla, esas respuestas caen a plantilla y el agente sigue funcionando.
"""

from typing import List, Optional, Tuple

from ..schemas.contracts import FluentDropOutput, MIN_CALIFICACION_RECOMENDADA
from ..scoring.mcda_engine import ScoredCandidate
from .extraccion import ETIQUETAS_CAMPO

MENSAJE_SIN_DATOS = (
    "No tengo suficiente información en mi base de conocimiento para responder esa pregunta."
)

PROMPT_CONVERSACION = """
Eres FluentDrop, un agente que ayuda a vendedores de dropshipping a comparar
proveedores y decidir a cuál comprarle.

Lo que sabes hacer:
- Comparar proveedores por precio, tiempo de envío, calificación y stock.
- Explicar por qué un proveedor quedó por encima de otro.
- Advertir cuando un proveedor tiene señales de riesgo en sus reseñas.

Lo que no haces nunca: comprar, cambiar la configuración de una tienda o
contactar proveedores. Solo recomiendas; la decisión es del comerciante.

Reglas de escritura:
- Español neutro, tono directo y cordial. Tutea.
- Máximo tres frases. Sin listas, sin encabezados, sin emojis.
- NUNCA inventes datos de proveedores, precios, calificaciones ni enlaces.
- Si te preguntan algo ajeno a proveedores, productos, envíos o dropshipping,
  dilo con naturalidad y reconduce a lo que sí puedes hacer.
- No prometas acciones: no vas a comprar ni a contactar a nadie.
"""


def _dinero(valor: Optional[float]) -> str:
    return f"${valor:,.2f} USD" if valor is not None else "precio sin confirmar"


def _plural(cantidad: int, singular: str, plural: str) -> str:
    return singular if cantidad == 1 else plural


# ═══════════════════════════════════════════════════════════════════════════
# Veredicto — determinista, desde el contrato validado
# ═══════════════════════════════════════════════════════════════════════════

def redactar_veredicto(
    contrato: FluentDropOutput,
    ranking: List[ScoredCandidate],
    correcciones: Optional[List[str]] = None,
) -> str:
    """
    Escribe el veredicto leyendo el contrato que salió de Gate 4.

    Cada cifra de la frase sale de un campo validado. Si Gate 4 anuló la
    recomendación, aquí no hay forma de reconstruirla.
    """
    partes: List[str] = []

    if contrato.proveedor_recomendado:
        encabezado = f"Te conviene **{contrato.proveedor_recomendado}**"
        detalles = []
        if contrato.precio is not None:
            detalles.append(_dinero(contrato.precio))
        if contrato.tiempo_envio_estimado:
            detalles.append(f"envío de {contrato.tiempo_envio_estimado}")
        if contrato.calificacion is not None:
            detalles.append(f"calificación {contrato.calificacion}/5")
        if contrato.stock_disponible is not None:
            detalles.append(f"{contrato.stock_disponible} unidades en stock")

        if detalles:
            encabezado += ": " + ", ".join(detalles) + "."
        else:
            encabezado += "."
        partes.append(encabezado)

        # Por qué ganó: se explica con los puntajes del MCDA, que son
        # deterministas y reproducibles, no con una justificación generada.
        ganador = next(
            (sc for sc in ranking if sc.candidate.nombre == contrato.proveedor_recomendado),
            None,
        )
        if ganador is not None:
            motivos = _motivos_mcda(ganador, ranking)
            if motivos:
                partes.append(f"Gana por {motivos}.")

        if contrato.url_producto:
            partes.append(f"Enlace al producto: {contrato.url_producto}")

    else:
        partes.append(_explicar_sin_recomendacion(ranking))

    if contrato.alternativas:
        nombres = []
        for alternativa in contrato.alternativas[:3]:
            if alternativa.precio is not None:
                nombres.append(f"{alternativa.nombre} ({_dinero(alternativa.precio)})")
            else:
                nombres.append(alternativa.nombre)
        etiqueta = _plural(len(nombres), "Otra opción evaluada", "Otras opciones evaluadas")
        partes.append(f"{etiqueta}: {', '.join(nombres)}.")

    riesgosos = [sc for sc in ranking if sc.tiene_riesgo_critico]
    if riesgosos:
        detalle = "; ".join(
            f"{sc.candidate.nombre} — {sc.motivo_riesgo}" for sc in riesgosos[:2]
        )
        partes.append(f"Ojo con las reseñas: {detalle}.")

    if correcciones:
        partes.append(
            "Descarté algún dato que no encontré en tu mensaje, así que no lo usé para decidir: "
            + "; ".join(correcciones[:2])
            + "."
        )

    if contrato.requiere_revision_humana:
        partes.append("Esto queda marcado para que lo revises antes de comprar: yo no ejecuto la compra.")

    return "\n\n".join(partes)


def _motivos_mcda(ganador: ScoredCandidate, ranking: List[ScoredCandidate]) -> str:
    """
    Traduce a palabras los dos criterios en que el ganador saca más ventaja.

    Se comparan los subpuntajes con el promedio del resto, de modo que la
    explicación describe por qué ganó *este* caso y no una frase genérica.
    """
    resto = [sc for sc in ranking if sc is not ganador and sc.candidate.es_viable]
    if not resto:
        return "ser el único candidato que superó los filtros duros"

    criterios = [
        ("precio", ganador.score_precio, sum(sc.score_precio for sc in resto) / len(resto)),
        ("tiempo de envío", ganador.score_envio, sum(sc.score_envio for sc in resto) / len(resto)),
        ("calificación", ganador.score_rating, sum(sc.score_rating for sc in resto) / len(resto)),
        ("stock", ganador.score_stock, sum(sc.score_stock for sc in resto) / len(resto)),
    ]
    ventajas = sorted(
        ((nombre, propio - medio) for nombre, propio, medio in criterios),
        key=lambda par: par[1],
        reverse=True,
    )
    destacados = [nombre for nombre, delta in ventajas if delta > 0.05][:2]

    if not destacados:
        return f"el puntaje combinado más alto ({ganador.score_final:.2f} sobre 1.00)"
    if len(destacados) == 1:
        return f"{destacados[0]} ({ganador.score_final:.2f} sobre 1.00)"
    return f"{destacados[0]} y {destacados[1]} ({ganador.score_final:.2f} sobre 1.00)"


def _explicar_sin_recomendacion(ranking: List[ScoredCandidate]) -> str:
    """
    Dice por qué no hay recomendación, con el motivo concreto de cada descarte.

    Un "no puedo recomendarte nada" sin explicación obliga al comerciante a
    adivinar si el problema fue suyo o del sistema.
    """
    if not ranking:
        return (
            "No puedo recomendarte un proveedor: no recibí ninguno con datos suficientes "
            "para compararlo."
        )

    excluidos = [sc for sc in ranking if not sc.candidate.es_viable and sc.candidate.motivo_exclusion]
    if excluidos:
        detalle = "; ".join(
            f"{sc.candidate.nombre} ({sc.candidate.motivo_exclusion})" for sc in excluidos[:3]
        )
        return (
            "No puedo recomendarte ninguno de estos proveedores. "
            f"Los filtros duros los descartaron: {detalle}. "
            f"El mínimo para recomendar es calificación {MIN_CALIFICACION_RECOMENDADA} y stock disponible."
        )

    return (
        "No puedo recomendarte ninguno todavía: ninguno reúne las condiciones mínimas "
        f"(calificación {MIN_CALIFICACION_RECOMENDADA} o más, precio y stock confirmados)."
    )


# ═══════════════════════════════════════════════════════════════════════════
# Respuestas conversacionales — modelo con plantilla de respaldo
# ═══════════════════════════════════════════════════════════════════════════

PLANTILLAS = {
    "saludo": (
        "Hola. Soy FluentDrop: comparo proveedores de dropshipping y te digo a cuál "
        "conviene comprarle. Pásame los que estás mirando con su precio, tiempo de "
        "envío y calificación, y te doy un veredicto."
    ),
    "capacidades": (
        "Comparo proveedores de dropshipping por precio, tiempo de envío, calificación y "
        "stock, te digo cuál conviene y por qué, y te advierto si alguno tiene reseñas "
        "con señales de fraude. No compro ni cambio nada en tu tienda: la decisión es "
        "tuya. Escríbeme algo como «tengo dos proveedores para unos audífonos: uno a $8 "
        "con envío de 12 días y 4.5 estrellas, otro a $6 con 20 días y 4.1»."
    ),
    "fuera_de_dominio": (
        "Eso se sale de lo que manejo. Mi base son proveedores de dropshipping: precios, "
        "tiempos de envío, calificaciones y stock. Si me pasas proveedores para comparar, "
        "con eso sí te ayudo."
    ),
}


def redactar_conversacion(
    intencion: str,
    mensaje_usuario: str,
    servicio=None,
    historial: Optional[str] = None,
) -> Tuple[str, bool]:
    """
    Responde un turno que no lleva veredicto.

    Aquí sí escribe el modelo, porque no hay cifras de proveedores en juego y
    una respuesta natural hace al agente mucho más usable. La plantilla cubre
    el caso sin clave de API o con el servicio caído.

    Devuelve también si contestó el modelo, para que la interfaz pueda decir
    con qué motor se atendió el turno en vez de dar por hecho que fue el
    configurado.
    """
    respaldo = PLANTILLAS.get(intencion, PLANTILLAS["fuera_de_dominio"])

    if servicio is None or getattr(servicio, "client", None) is None:
        return respaldo, False

    contexto = (f"Conversación previa:\n{historial}\n\n" if historial else "") + (
        f"Mensaje del usuario:\n{mensaje_usuario}\n\n"
        f"Clasificación interna de este mensaje: {intencion}.\n"
        "Responde al usuario siguiendo tus reglas."
    )
    generada = servicio.completar_texto(PROMPT_CONVERSACION, contexto)
    return (generada, True) if generada else (respaldo, False)


def redactar_aclaracion(faltantes: List[str], servicio=None, mensaje_usuario: str = "") -> str:
    """
    Pide exactamente lo que falta para poder evaluar.

    La lista de faltantes la calcula `campos_faltantes` sobre la entrada ya
    anclada, así que la pregunta siempre se refiere a un dato que de verdad no
    está, y no a uno que el modelo creyó no ver.
    """
    if not faltantes:
        faltantes = ["proveedores"]

    etiquetas = [ETIQUETAS_CAMPO.get(campo, campo) for campo in faltantes]
    if len(etiquetas) == 1:
        pedido = etiquetas[0]
    else:
        pedido = ", ".join(etiquetas[:-1]) + " y " + etiquetas[-1]

    if faltantes == ["proveedores"]:
        return (
            "Para darte un veredicto necesito los proveedores que estás comparando. "
            "Pásame de cada uno el precio, cuánto tarda el envío y su calificación; "
            "con eso corro la evaluación completa."
        )

    return (
        f"Casi. Para evaluarlo me falta {pedido}. "
        "Dímelo y te doy el veredicto con el detalle de por qué."
    )
