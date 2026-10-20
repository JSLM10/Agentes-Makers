"""
conversacion — Capa conversacional de FluentDrop
=================================================

El pipeline de 6 gates espera un caso con formato fijo. Esta capa le quita esa
exigencia al usuario: recibe lo que haya escrito en lenguaje corriente y decide
qué hacer con ello, sin tocar ninguna de las garantías del pipeline.

    extraccion  texto libre        -> ProductInput + intención + anclaje
    redaccion   contrato validado  -> prosa que el comerciante puede leer
    agente      un turno completo  -> respuesta, traza de aduanas y evidencia
"""

from .agente import AgenteConversacional, Turno
from .extraccion import (
    INTENCION_CAPACIDADES,
    INTENCION_EVALUACION,
    INTENCION_FUERA_DE_DOMINIO,
    INTENCION_INCOMPLETA,
    INTENCION_SALUDO,
    Extraccion,
)

__all__ = [
    "AgenteConversacional",
    "Turno",
    "Extraccion",
    "INTENCION_EVALUACION",
    "INTENCION_SALUDO",
    "INTENCION_CAPACIDADES",
    "INTENCION_FUERA_DE_DOMINIO",
    "INTENCION_INCOMPLETA",
]
