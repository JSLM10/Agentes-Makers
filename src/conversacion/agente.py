"""
agente.py — Un turno de conversación, de principio a fin
=========================================================

Esta es la pieza que convierte a FluentDrop en algo a lo que se le puede
escribir. Recibe la conversación, decide qué clase de mensaje es el último y
responde en consecuencia:

    saludo / capacidades / fuera de dominio  ->  respuesta conversacional
    quiere evaluar pero faltan datos         ->  pregunta por lo que falta
    trae proveedores con datos               ->  PIPELINE COMPLETO DE 6 GATES

El pipeline no se tocó. Esta capa decide *cuándo* invocarlo y traduce su
contrato a prosa; las seis aduanas siguen gobernando todo lo que el usuario
lee sobre una recomendación.

Antes de cualquier ruta se corre Gate 1 sobre el mensaje. El pipeline ya lo
hace por dentro, pero una inyección metida en un "hola" nunca llegaría hasta
allí: el camino conversacional no pasa por el pipeline, y sin esta
comprobación sería la puerta sin vigilar de todo el sistema.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..gates.gates_engine import evaluate_gate1_ingestion
from ..pipeline_orchestrator import FluentDropPipeline, parse_raw_text_input
from ..schemas.contracts import GateResult
from ..services.llm_synthesis import LLMSynthesisService
from . import extraccion as lectura
from . import redaccion

# Nombres de las aduanas en el lenguaje de despacho que ya usa la interfaz.
NOMBRES_GATE = {
    "GA": "Anclaje de la lectura",
    "G0": "Pre-vuelo y credenciales",
    "G1": "Integridad del manifiesto",
    "G2": "Filtros duros de negocio",
    "G3": "Triaje de viabilidad",
    "G4": "Contrato y guardarraíles",
    "G5": "Despacho e idempotencia",
}

TIPO_VEREDICTO = "veredicto"
TIPO_ACLARACION = "aclaracion"
TIPO_CONVERSACION = "conversacion"
TIPO_RECHAZO = "rechazo"


@dataclass
class Turno:
    """Todo lo que la interfaz necesita para pintar una respuesta."""

    respuesta: str
    tipo: str
    traza: List[Dict[str, Any]] = field(default_factory=list)
    contrato: Optional[Dict[str, Any]] = None
    evidencia: List[Dict[str, Any]] = field(default_factory=list)
    modelo: Optional[str] = None
    duracion_ms: float = 0.0
    requiere_revision: bool = False

    def a_dict(self) -> Dict[str, Any]:
        return {
            "respuesta": self.respuesta,
            "tipo": self.tipo,
            "traza": self.traza,
            "contrato": self.contrato,
            "evidencia": self.evidencia,
            "modelo": self.modelo,
            "duracion_ms": round(self.duracion_ms, 2),
            "requiere_revision": self.requiere_revision,
        }


def _paso(gate: GateResult, latencia_ms: float) -> Dict[str, Any]:
    """Un gate, en la forma que consume la interfaz."""
    return {
        "id": gate.gate_id,
        "nombre": NOMBRES_GATE.get(gate.gate_id, gate.gate_name),
        "tecnico": gate.gate_name,
        "passed": gate.passed,
        "critical": gate.critical,
        "motivo": gate.reason,
        "detalles": gate.details,
        "latencia_ms": round(latencia_ms, 2),
    }


def _paso_anclaje(extraida: lectura.Extraccion, latencia_ms: float) -> Dict[str, Any]:
    """
    La aduana propia de la capa conversacional.

    No es uno de los seis gates del pipeline: es la que vigila la *entrada*
    que produjo la lectura con modelo, antes de que el pipeline la reciba.
    """
    limpio = not extraida.correcciones
    if extraida.fuente == "modelo":
        motivo = (
            "Toda cifra leída aparece literalmente en el mensaje"
            if limpio
            else f"{len(extraida.correcciones)} dato(s) descartado(s) por no estar en el mensaje"
        )
    else:
        motivo = "Lectura determinista por expresiones regulares, sin modelo de por medio"

    return {
        "id": "GA",
        "nombre": NOMBRES_GATE["GA"],
        "tecnico": "Extraction Anchoring",
        "passed": limpio,
        "critical": False,
        "motivo": motivo,
        "detalles": {"fuente": extraida.fuente, "correcciones": extraida.correcciones},
        "latencia_ms": round(latencia_ms, 2),
    }


def _evidencia(ranking) -> List[Dict[str, Any]]:
    """
    El desglose MCDA de cada candidato.

    Cumple el papel que en un RAG cumplen las fuentes: deja que el usuario
    compruebe por su cuenta de dónde salió el veredicto, en vez de pedirle
    que confíe en la frase.
    """
    return [
        {
            "nombre": sc.candidate.nombre,
            "rank": sc.rank,
            "precio": sc.candidate.precio,
            "envio": sc.candidate.tiempo_envio_estimado,
            "calificacion": sc.candidate.calificacion,
            "stock": sc.candidate.stock_disponible,
            "url": sc.candidate.url,
            "viable": sc.candidate.es_viable,
            "motivo_exclusion": sc.candidate.motivo_exclusion,
            "score_final": sc.score_final,
            "subpuntajes": {
                "precio": sc.score_precio,
                "envio": sc.score_envio,
                "calificacion": sc.score_rating,
                "stock": sc.score_stock,
            },
            "penalizacion_riesgo": sc.penalizacion_riesgo,
            "motivo_riesgo": sc.motivo_riesgo,
        }
        for sc in ranking
    ]


class AgenteConversacional:
    """Fachada de un turno: entra una conversación, sale una respuesta."""

    def __init__(
        self,
        pipeline: Optional[FluentDropPipeline] = None,
        servicio: Optional[LLMSynthesisService] = None,
        offline: bool = False,
    ):
        """
        `offline=True` fuerza el camino determinista de punta a punta: ni la
        lectura ni la redacción ni la síntesis llaman al modelo. Es el modo
        seguro para demostrar en vivo sin depender de la red.
        """
        self.servicio = servicio or LLMSynthesisService(offline=offline)
        self.pipeline = pipeline or FluentDropPipeline(llm_service=self.servicio)
        self.offline = offline

    # -- utilidades de conversación -------------------------------------

    @staticmethod
    def _historial(mensajes: List[Dict[str, str]]) -> str:
        """
        Los turnos previos, en texto, para que la lectura tenga memoria.

        Importa para el caso corriente: el usuario da el precio en un mensaje
        y la calificación en el siguiente, y espera que el agente los junte.
        """
        lineas = []
        for mensaje in mensajes[:-1][-8:]:
            rol = "Usuario" if mensaje.get("rol") == "usuario" else "FluentDrop"
            contenido = (mensaje.get("contenido") or "").strip()
            if contenido:
                lineas.append(f"{rol}: {contenido}")
        return "\n".join(lineas)

    @staticmethod
    def _texto_del_usuario(mensajes: List[Dict[str, str]]) -> str:
        """
        Solo lo que escribió el usuario, que es contra lo que se ancla.

        Anclar contra la conversación entera sería un agujero: el agente pudo
        haber mencionado una cifra en un turno anterior, y entonces el modelo
        podría "leerla" del input sin que el usuario la haya dicho nunca.
        """
        return "\n".join(
            (m.get("contenido") or "").strip()
            for m in mensajes
            if m.get("rol") == "usuario" and (m.get("contenido") or "").strip()
        )

    # -- el turno --------------------------------------------------------

    def responder(self, mensajes: List[Dict[str, str]]) -> Turno:
        """Procesa el último mensaje de la conversación y devuelve la respuesta."""
        inicio = time.perf_counter()

        if not mensajes or not (mensajes[-1].get("contenido") or "").strip():
            return Turno(
                respuesta="Escríbeme algo y lo miramos.",
                tipo=TIPO_CONVERSACION,
                duracion_ms=0.0,
            )

        ultimo = mensajes[-1]["contenido"].strip()
        historial = self._historial(mensajes)
        texto_usuario = self._texto_del_usuario(mensajes)

        # --- Gate 1 adelantado: ninguna ruta se salta la revisión de inyección
        marca = time.perf_counter()
        g1_previo, inyeccion = evaluate_gate1_ingestion(ultimo)
        latencia_g1 = (time.perf_counter() - marca) * 1000.0

        # --- Lectura: qué quiere y con qué datos
        marca = time.perf_counter()
        extraida = lectura.extraer(
            ultimo,
            servicio=None if self.offline else self.servicio,
            historial=historial or None,
            texto_anclaje=texto_usuario,
        )
        latencia_lectura = (time.perf_counter() - marca) * 1000.0

        # --- Intento de inyección
        #
        # No se corta aquí. Se deja atravesar el pipeline completo para que la
        # traza muestre dónde lo detuvo cada aduana: Gate 1 lo marca, Gate 2 no
        # encuentra candidatos válidos y Gate 3 cortocircuita sin gastar una
        # sola llamada al modelo. Un rechazo temprano sería igual de seguro,
        # pero escondería la parte del sistema que lo hace seguro.
        #
        # Lo que sí se sustituye es el texto: el usuario lee el rechazo, no un
        # veredicto. Y el contrato sigue siendo el que validó Gate 4.
        if inyeccion and not g1_previo.passed:
            entrada = extraida.entrada or parse_raw_text_input(ultimo)
            turno = self._evaluar(
                lectura.Extraccion(
                    intencion=lectura.INTENCION_EVALUACION,
                    entrada=entrada,
                    correcciones=extraida.correcciones,
                    fuente=extraida.fuente,
                ),
                inicio,
                latencia_lectura,
                ultimo,
            )
            turno.tipo = TIPO_RECHAZO
            turno.requiere_revision = True
            turno.respuesta = (
                "Ese mensaje intenta que ejecute una acción o que ignore mis reglas, "
                "así que no lo acato. Yo solo comparo proveedores y recomiendo: no hago "
                "compras, no contacto proveedores y no cambio nada en tu tienda. "
                "Si quieres, pásame los proveedores que estás evaluando y te doy el veredicto."
            )
            return turno

        # --- Rutas sin veredicto
        if extraida.intencion in (
            lectura.INTENCION_SALUDO,
            lectura.INTENCION_CAPACIDADES,
            lectura.INTENCION_FUERA_DE_DOMINIO,
        ):
            respuesta, uso_modelo = redaccion.redactar_conversacion(
                extraida.intencion,
                ultimo,
                servicio=None if self.offline else self.servicio,
                historial=historial or None,
            )
            return Turno(
                respuesta=respuesta,
                tipo=TIPO_CONVERSACION,
                traza=[_paso(g1_previo, latencia_g1), _paso_anclaje(extraida, latencia_lectura)],
                modelo=self._etiqueta_modelo(uso_modelo),
                duracion_ms=(time.perf_counter() - inicio) * 1000.0,
            )

        if extraida.intencion == lectura.INTENCION_INCOMPLETA or not extraida.tiene_candidatos:
            return Turno(
                respuesta=redaccion.redactar_aclaracion(
                    extraida.faltantes,
                    servicio=None if self.offline else self.servicio,
                    mensaje_usuario=ultimo,
                ),
                tipo=TIPO_ACLARACION,
                traza=[_paso(g1_previo, latencia_g1), _paso_anclaje(extraida, latencia_lectura)],
                modelo=self._etiqueta_modelo(extraida.fuente == "modelo"),
                duracion_ms=(time.perf_counter() - inicio) * 1000.0,
            )

        # --- Ruta con veredicto: el pipeline completo
        return self._evaluar(extraida, inicio, latencia_lectura, ultimo)

    def _evaluar(
        self,
        extraida: lectura.Extraccion,
        inicio: float,
        latencia_lectura: float,
        texto_crudo: str,
    ) -> Turno:
        """
        Corre las seis aduanas y redacta el veredicto desde el contrato.

        Al pipeline se le entrega la entrada ya estructurada —para que Gate 2
        marque sobre los mismos objetos que luego se rankean— y además el
        texto tal como lo escribió el usuario, que es lo que debe inspeccionar
        Gate 1.
        """
        traza: List[Dict[str, Any]] = [_paso_anclaje(extraida, latencia_lectura)]
        ultimo_corte = time.perf_counter()

        def al_resolver(gate: GateResult) -> None:
            nonlocal ultimo_corte
            ahora = time.perf_counter()
            traza.append(_paso(gate, (ahora - ultimo_corte) * 1000.0))
            ultimo_corte = ahora

        informe = self.pipeline.run(
            extraida.entrada,
            on_gate=al_resolver,
            raw_text_override=texto_crudo,
        )
        ranking = self.pipeline.scoring.rank_candidates(
            extraida.entrada.proveedores_candidatos,
            ref_price=(
                extraida.entrada.proveedor_actual.precio
                if extraida.entrada.proveedor_actual
                else None
            ),
        )

        respuesta = redaccion.redactar_veredicto(
            informe.output,
            ranking,
            correcciones=extraida.correcciones,
        )

        return Turno(
            respuesta=respuesta,
            tipo=TIPO_VEREDICTO,
            traza=traza,
            contrato=informe.output.model_dump(),
            evidencia=_evidencia(ranking),
            modelo=informe.model_used,
            duracion_ms=(time.perf_counter() - inicio) * 1000.0,
            requiere_revision=informe.output.requiere_revision_humana,
        )

    def _etiqueta_modelo(self, respondio_el_modelo: bool) -> str:
        """
        Qué motor atendió de verdad el turno.

        Se pasa el resultado real de la llamada en vez de deducirlo de la
        configuración: con una clave válida pero un modelo que devuelve 404,
        la respuesta la escribió la plantilla, y rotularla con el nombre del
        modelo sería decir que contestó algo que nunca contestó.
        """
        if self.offline:
            return "deterministic_offline"
        if not self.servicio.client:
            return "deterministic_fallback_no_key"
        return self.servicio.model if respondio_el_modelo else "deterministic_fallback"
