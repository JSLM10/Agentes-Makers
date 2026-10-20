"""
extraccion.py — De lenguaje natural a ProductInput
===================================================

FluentDrop nació esperando un caso con formato fijo. Este módulo le quita esa
exigencia al usuario: recibe lo que haya escrito —una frase suelta, una lista
pegada o una conversación de varios turnos— y produce el `ProductInput` que el
pipeline de 6 gates ya sabe procesar.

Extraer con un modelo abre una vía de alucinación que el pipeline no tenía:
inventar un precio o una calificación que el usuario nunca dijo. Por eso toda
cifra extraída pasa por `verificar_anclaje`, que comprueba que aparezca
literalmente en el texto de origen; la que no aparece se anula y queda anotada.
Es el mismo criterio que el pipeline ya aplica al enlace del producto: los
datos del catálogo se copian, no se infieren.
"""

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from ..pipeline_orchestrator import parse_raw_text_input
from ..schemas.contracts import CurrentSupplier, ProductInput, SupplierCandidate

# --- Intenciones que el agente sabe atender ---
INTENCION_EVALUACION = "evaluacion"
INTENCION_INCOMPLETA = "incompleta"
INTENCION_SALUDO = "saludo"
INTENCION_CAPACIDADES = "capacidades"
INTENCION_FUERA_DE_DOMINIO = "fuera_de_dominio"

INTENCIONES_VALIDAS = {
    INTENCION_EVALUACION,
    INTENCION_INCOMPLETA,
    INTENCION_SALUDO,
    INTENCION_CAPACIDADES,
    INTENCION_FUERA_DE_DOMINIO,
}

# Campos sin los cuales no tiene sentido pedirle un veredicto al pipeline.
CAMPOS_MINIMOS = ("precio", "tiempo_envio_estimado", "calificacion")

ETIQUETAS_CAMPO = {
    "proveedores": "qué proveedores quieres comparar",
    "producto": "qué producto es",
    "precio": "el precio por unidad",
    "tiempo_envio_estimado": "cuánto tarda el envío",
    "calificacion": "la calificación del proveedor",
    "stock_disponible": "cuántas unidades tiene en stock",
}


PROMPT_EXTRACCION = """
Eres el componente de lectura de FluentDrop, un sistema que compara proveedores
de dropshipping.

Tu única tarea es leer lo que escribió el usuario y devolver un JSON con los
datos de proveedores que estén EXPLÍCITAMENTE en el texto. No evalúas, no
recomiendas y no opinas: solo transcribes a una estructura.

Reglas obligatorias:
- Devuelve ÚNICAMENTE un objeto JSON válido, sin bloques markdown.
- NUNCA inventes una cifra. Si el usuario no dijo el precio, el precio es null.
- Copia los números tal como aparecen en el texto.
- Copia las URL carácter por carácter. Si no hay URL, es null.
- Si el usuario describe un solo proveedor sin nombrarlo, llámalo "Proveedor consultado".
- Todo proveedor sobre el que el usuario pida una opinión va en
  "proveedores_candidatos", incluso si dice "el proveedor" o "mi proveedor".
- "proveedor_actual" es SOLO para el que ya le compra y menciona como punto de
  comparación frente a otros. Si no hay otros, no uses este campo.
- "proveedores_candidatos" nunca puede quedar vacío si el usuario dio datos de
  algún proveedor.
- Lee toda la conversación: el usuario puede haber dado el precio en un turno
  y la calificación en otro.

Clasifica la intención del ÚLTIMO mensaje del usuario en una de estas:
- "evaluacion": pide comparar, evaluar o decidir sobre proveedores y dio datos.
- "incompleta": quiere una evaluación pero faltan datos para hacerla.
- "saludo": saluda, agradece o se despide.
- "capacidades": pregunta qué eres o qué sabes hacer.
- "fuera_de_dominio": pregunta algo ajeno a proveedores, productos, envíos o
  dropshipping.

Esquema exacto:
{
  "intencion": "evaluacion | incompleta | saludo | capacidades | fuera_de_dominio",
  "producto": "string | null",
  "categoria": "string | null",
  "proveedor_actual": {
    "nombre": "string",
    "precio": "number | null",
    "tiempo_envio_estimado": "string | null",
    "calificacion": "number | null",
    "stock_disponible": "integer | null"
  } | null,
  "proveedores_candidatos": [
    {
      "nombre": "string",
      "precio": "number | null",
      "tiempo_envio_estimado": "string | null",
      "calificacion": "number | null",
      "stock_disponible": "integer | null",
      "resenas": "string | null",
      "url": "string | null"
    }
  ]
}
"""


@dataclass
class Extraccion:
    """Lo que la capa de lectura entendió de la conversación."""

    intencion: str
    entrada: Optional[ProductInput] = None
    faltantes: List[str] = field(default_factory=list)
    correcciones: List[str] = field(default_factory=list)
    fuente: str = "determinista"

    @property
    def tiene_candidatos(self) -> bool:
        return bool(self.entrada and self.entrada.proveedores_candidatos)


# ═══════════════════════════════════════════════════════════════════════════
# Verificación de anclaje
# ═══════════════════════════════════════════════════════════════════════════

def _normalizar(texto: str) -> str:
    """Unifica separadores para que 1.299,50 y 1299.50 se busquen igual."""
    limpio = texto.lower()
    limpio = re.sub(r"(\d)[ \s](\d{3}\b)", r"\1\2", limpio)
    limpio = re.sub(r"(\d),(\d)", r"\1.\2", limpio)
    return limpio


def _variantes_numero(valor: float) -> List[str]:
    """
    Formas en que un mismo número puede estar escrito en el texto.

    La forma con dos decimales no es decorativa: un precio de 1.20 se guarda
    como 1.2, y buscar solo "1.2" falla contra el "$1.20" que escribió el
    usuario, porque el 0 siguiente rompe el límite de la cifra.
    """
    numero = float(valor)
    variantes = {f"{numero:g}", f"{numero:.2f}"}
    if numero.is_integer():
        entero = int(numero)
        variantes.update({str(entero), f"{entero}.0", f"{entero}.00"})
    recortado = f"{numero:.2f}".rstrip("0").rstrip(".")
    if recortado:
        variantes.add(recortado)
    return sorted(variantes)


def _numero_presente(valor: Optional[float], texto: str) -> bool:
    """
    True si el número aparece como cifra independiente en el texto.

    Los límites importan: sin ellos, un precio inventado de 8 se daría por
    bueno porque el texto menciona 18 días de envío.
    """
    if valor is None:
        return True
    for variante in _variantes_numero(valor):
        patron = rf"(?<![\d.,]){re.escape(variante)}(?![\d]|[.,]\d)"
        if re.search(patron, texto):
            return True
    return False


def _texto_presente(valor: Optional[str], texto: str) -> bool:
    """True si todas las cifras de un texto libre ('8-12 días') están ancladas."""
    if not valor:
        return True
    numeros = re.findall(r"\d+(?:\.\d+)?", _normalizar(valor))
    return all(_numero_presente(float(n), texto) for n in numeros)


def verificar_anclaje(entrada: ProductInput, texto_fuente: str) -> Tuple[ProductInput, List[str]]:
    """
    Anula toda cifra o enlace que no aparezca literalmente en lo que escribió
    el usuario, y devuelve la lista de lo que tuvo que corregir.

    Es la aduana propia de esta capa. El pipeline ya vigila la salida del
    modelo; esto vigila su entrada, que es donde la lectura asistida por IA
    puede colar un dato que nadie dijo.
    """
    fuente = _normalizar(texto_fuente)
    correcciones: List[str] = []

    def revisar(obj: Any, etiqueta: str) -> None:
        for campo in ("precio", "calificacion", "stock_disponible"):
            valor = getattr(obj, campo, None)
            if valor is not None and not _numero_presente(float(valor), fuente):
                setattr(obj, campo, None)
                correcciones.append(
                    f"{etiqueta}: se descartó {campo}={valor}, no aparece en el mensaje"
                )

        envio = getattr(obj, "tiempo_envio_estimado", None)
        if envio and not _texto_presente(envio, fuente):
            obj.tiempo_envio_estimado = None
            correcciones.append(
                f"{etiqueta}: se descartó el envío '{envio}', no aparece en el mensaje"
            )

        url = getattr(obj, "url", None)
        if url and url.lower() not in fuente:
            obj.url = None
            correcciones.append(f"{etiqueta}: se descartó un enlace que no estaba en el mensaje")

    if entrada.proveedor_actual:
        revisar(entrada.proveedor_actual, f"Proveedor actual '{entrada.proveedor_actual.nombre}'")
    for candidato in entrada.proveedores_candidatos:
        revisar(candidato, f"'{candidato.nombre}'")

    return entrada, correcciones


def campos_faltantes(entrada: Optional[ProductInput]) -> List[str]:
    """Qué le falta a la entrada para que el pipeline pueda dar un veredicto."""
    if entrada is None or not entrada.proveedores_candidatos:
        return ["proveedores"]

    faltan: List[str] = []
    if not entrada.producto or entrada.producto == "Producto Desconocido":
        faltan.append("producto")
    for campo in CAMPOS_MINIMOS:
        if all(getattr(c, campo, None) is None for c in entrada.proveedores_candidatos):
            faltan.append(campo)
    return faltan


# ═══════════════════════════════════════════════════════════════════════════
# Lectura determinista — funciona sin red y sin clave de API
# ═══════════════════════════════════════════════════════════════════════════

# Saludos y cortesías: se reconocen por sí solos y no ameritan llamar al modelo.
PATRONES_SALUDO = [
    r"^\s*(hola|buenas|buen d[ií]a|buenas tardes|buenas noches|hey|qu[eé] tal)\b",
    r"^\s*(gracias|muchas gracias|mil gracias|listo|perfecto|ok|vale)\s*[.!]*\s*$",
    r"^\s*(chao|adi[oó]s|hasta luego|nos vemos)\b",
]

PATRONES_CAPACIDADES = [
    r"qu[eé]\s+(puedes|sabes|haces|eres)",
    r"qui[eé]n\s+eres",
    r"c[oó]mo\s+(funcionas|te\s+uso|trabajas)",
    r"para\s+qu[eé]\s+sirves",
    r"\bayuda\b\s*$",
    r"^\s*/?ayuda\s*$",
]

# Señales de que el mensaje habla del dominio, aunque no traiga cifras.
PATRONES_DOMINIO = [
    r"proveedor",
    r"dropship",
    r"env[ií]o",
    r"stock",
    r"calificaci[oó]n",
    r"\brating\b",
    r"producto",
    r"precio",
    r"catalogo|cat[aá]logo",
    r"aliexpress|alibaba|cj\s*dropshipping|dropi",
    r"comprar|vender|tienda",
]

# Unidades que delatan un dato aprovechable dentro de la prosa.
RE_PRECIO = re.compile(
    r"(?:\$|usd\s*)(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*(?:usd|d[oó]lares|dolares)",
    re.IGNORECASE,
)
RE_ENVIO = re.compile(
    r"(?:env[ií]o|entrega|tarda|demora|llega|tr[aá]nsito|shipping)\D{0,20}?"
    r"(\d+(?:[.,]\d+)?(?:\s*(?:-|a|al)\s*\d+(?:[.,]\d+)?)?)\s*d[ií]as?"
    r"|(\d+(?:[.,]\d+)?(?:\s*(?:-|a|al)\s*\d+(?:[.,]\d+)?)?)\s*d[ií]as?",
    re.IGNORECASE,
)
RE_CALIFICACION = re.compile(
    r"(?:calificaci[oó]n|rating|puntaje|estrellas|valoraci[oó]n)\D{0,12}?(\d(?:[.,]\d+)?)"
    r"|(\d(?:[.,]\d+)?)\s*(?:/\s*5|de\s*5|estrellas)",
    re.IGNORECASE,
)
RE_STOCK = re.compile(
    r"(?:stock|inventario|unidades|disponibles?)\D{0,12}?(\d+)|(\d+)\s*unidades",
    re.IGNORECASE,
)
RE_URL = re.compile(r"https?://\S+")

# "el proveedor TWS9D", "proveedor: MiniSlim", "con AliExpress"
RE_NOMBRE = re.compile(
    r"proveedor(?:\s+llamado)?\s*:?\s*([A-Z0-9][\w\-]*(?:\s+[A-Z0-9][\w\-]*)?)",
)


def _a_float(texto: Optional[str]) -> Optional[float]:
    if not texto:
        return None
    try:
        return float(texto.replace(",", "."))
    except ValueError:
        return None


def _primer_grupo(match: Optional[re.Match]) -> Optional[str]:
    """El primer grupo con contenido de un patrón con alternativas."""
    if not match:
        return None
    for grupo in match.groups():
        if grupo:
            return grupo
    return None


def _parece_estructurado(texto: str) -> bool:
    """¿Viene con el formato de los casos de `casos/`?"""
    return bool(
        re.search(r"producto\s*(?:en\s*tienda)?\s*:", texto, re.IGNORECASE)
        and re.search(r"proveedor(?:es)?\s+(?:candidatos?|actual)\s*:", texto, re.IGNORECASE)
    )


# Conectores que de verdad separan a un proveedor del siguiente.
RE_SEPARADOR = re.compile(
    r"\s*;\s*(?:y\s+)?|\s+y\s+(?:el\s+|la\s+)?(?:otro|otra|segundo|proveedor\b)|"
    r"\s+mientras\s+que\s+|\s+en\s+cambio\s+",
    re.IGNORECASE,
)


def _segmentar(texto: str) -> List[str]:
    """
    Parte el mensaje en trozos que podrían describir un proveedor cada uno.

    Primero por líneas y viñetas, que es como suele pegarse un catálogo; si
    todo vino en un párrafo, por los conectores que separan proveedores.
    """
    lineas = [ln.strip(" \t-•*") for ln in texto.splitlines() if ln.strip(" \t-•*")]
    con_datos = [ln for ln in lineas if RE_PRECIO.search(ln) or RE_ENVIO.search(ln)]
    if len(con_datos) > 1:
        return con_datos

    base = con_datos[0] if len(con_datos) == 1 else texto
    partes = [p.strip() for p in RE_SEPARADOR.split(base) if p and p.strip()]
    return partes if len(partes) > 1 else [base]


def _leer_proveedor(segmento: str, indice: int) -> Optional[Tuple[SupplierCandidate, bool]]:
    """
    Saca de un trozo de prosa los atributos de un proveedor.

    Devuelve también si el nombre venía escrito o si hubo que inventarle uno
    de relleno: de eso depende que dos trozos puedan fusionarse después.
    """
    precio = _a_float(_primer_grupo(RE_PRECIO.search(segmento)))
    envio_crudo = _primer_grupo(RE_ENVIO.search(segmento))
    calificacion = _a_float(_primer_grupo(RE_CALIFICACION.search(segmento)))
    stock_crudo = _primer_grupo(RE_STOCK.search(segmento))
    url = RE_URL.search(segmento)

    if precio is None and envio_crudo is None and calificacion is None:
        return None

    nombre_match = RE_NOMBRE.search(segmento)
    nombre_explicito = bool(nombre_match)
    if nombre_match:
        nombre = nombre_match.group(1).strip()
    elif indice == 0:
        nombre = "Proveedor consultado"
    else:
        nombre = f"Proveedor {chr(ord('A') + indice)}"

    # Una calificación por encima de 5 no es una calificación; casi siempre es
    # un precio o un stock que cayó en el patrón por estar cerca de la palabra.
    if calificacion is not None and not 0.0 <= calificacion <= 5.0:
        calificacion = None

    candidato = SupplierCandidate(
        nombre=nombre,
        precio=precio,
        tiempo_envio_estimado=f"{envio_crudo} días" if envio_crudo else None,
        calificacion=calificacion,
        stock_disponible=int(stock_crudo) if stock_crudo else None,
        resenas=segmento,
        url=url.group(0).rstrip(".,;)") if url else None,
    )
    return candidato, nombre_explicito


ATRIBUTOS = ("precio", "tiempo_envio_estimado", "calificacion", "stock_disponible")


def _son_disjuntos(candidatos: List[SupplierCandidate]) -> bool:
    """¿Ningún atributo está informado en más de un candidato?"""
    for atributo in ATRIBUTOS:
        informados = sum(1 for c in candidatos if getattr(c, atributo) is not None)
        if informados > 1:
            return False
    return True


def _fusionar(candidatos: List[SupplierCandidate]) -> SupplierCandidate:
    """Junta en uno solo varios trozos que describen al mismo proveedor."""
    fusionado = candidatos[0]
    for otro in candidatos[1:]:
        for atributo in ATRIBUTOS + ("url",):
            if getattr(fusionado, atributo) is None:
                setattr(fusionado, atributo, getattr(otro, atributo))
    fusionado.resenas = " ".join(c.resenas for c in candidatos if c.resenas)
    return fusionado


def _leer_producto(texto: str) -> Optional[str]:
    patrones = [
        r"producto\s*(?:en\s*tienda)?\s*:\s*([^\n\r.,;]+)",
        r"(?:vendo|vender|tengo|para)\s+(?:unos?\s+|unas?\s+|el\s+|la\s+|los\s+|las\s+)?"
        r"([a-záéíóúñ][\w\s]{3,40}?)(?=\s+(?:y|que|con|de\s+un|del?\s+proveedor)|[.,;\n]|$)",
    ]
    for patron in patrones:
        match = re.search(patron, texto, re.IGNORECASE)
        if match:
            candidato = match.group(1).strip()
            if 3 < len(candidato) < 60:
                return candidato
    return None


def clasificar_deterministico(texto: str) -> str:
    """Intención por reglas, sin llamar al modelo."""
    limpio = texto.strip().lower()

    for patron in PATRONES_CAPACIDADES:
        if re.search(patron, limpio):
            return INTENCION_CAPACIDADES
    for patron in PATRONES_SALUDO:
        if re.search(patron, limpio):
            return INTENCION_SALUDO

    # Un mensaje puede traer datos de proveedor sin usar ninguna palabra del
    # dominio ("cuesta 8 dólares y tarda 12 días"). Las cifras con unidad son
    # señal suficiente, y sin esto ese turno se rechazaría como fuera de tema.
    trae_datos = bool(
        RE_PRECIO.search(texto) or RE_ENVIO.search(texto) or RE_CALIFICACION.search(texto)
    )
    del_dominio = trae_datos or any(re.search(p, limpio) for p in PATRONES_DOMINIO)
    if not del_dominio:
        return INTENCION_FUERA_DE_DOMINIO
    return INTENCION_EVALUACION


def extraer_deterministico(texto: str, texto_completo: Optional[str] = None) -> Extraccion:
    """
    Lectura sin modelo. Es el camino que corre cuando no hay clave de API, y
    también la red de seguridad si la llamada al modelo falla.

    La intención se decide sobre el último mensaje, pero los datos se buscan
    en `texto_completo` —todo lo que el usuario ha escrito en la conversación—
    porque es corriente que dé el precio en un turno y el envío en el
    siguiente, y espere que el agente los junte.
    """
    intencion = clasificar_deterministico(texto)
    if intencion in (INTENCION_SALUDO, INTENCION_CAPACIDADES, INTENCION_FUERA_DE_DOMINIO):
        return Extraccion(intencion=intencion, fuente="determinista")

    cuerpo = texto_completo or texto

    # El formato de los casos de demostración ya tiene su propio parser probado.
    if _parece_estructurado(cuerpo):
        entrada = parse_raw_text_input(cuerpo)
        if entrada.proveedores_candidatos:
            return Extraccion(
                intencion=INTENCION_EVALUACION,
                entrada=entrada,
                faltantes=campos_faltantes(entrada),
                fuente="determinista_estructurado",
            )

    candidatos: List[SupplierCandidate] = []
    explicitos: List[bool] = []
    for indice, segmento in enumerate(_segmentar(cuerpo)):
        leido = _leer_proveedor(segmento, indice)
        if leido:
            candidato, nombre_explicito = leido
            candidatos.append(candidato)
            explicitos.append(nombre_explicito)

    if not candidatos:
        return Extraccion(
            intencion=INTENCION_INCOMPLETA,
            faltantes=["proveedores"],
            fuente="determinista",
        )

    # Varios trozos sin nombre propio y con atributos que no se pisan son un
    # único proveedor descrito a lo largo de la conversación, no varios.
    if len(candidatos) > 1 and not any(explicitos) and _son_disjuntos(candidatos):
        candidatos = [_fusionar(candidatos)]

    entrada = ProductInput(
        producto=_leer_producto(cuerpo) or "Producto consultado",
        categoria=None,
        proveedor_actual=None,
        proveedores_candidatos=candidatos,
    )
    faltan = campos_faltantes(entrada)
    return Extraccion(
        intencion=INTENCION_EVALUACION if not faltan else INTENCION_INCOMPLETA,
        entrada=entrada,
        faltantes=faltan,
        fuente="determinista",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Lectura con modelo
# ═══════════════════════════════════════════════════════════════════════════

def _construir_entrada(datos: dict) -> Optional[ProductInput]:
    """Convierte el JSON del modelo en un ProductInput, tolerando basura."""
    candidatos: List[SupplierCandidate] = []
    for crudo in datos.get("proveedores_candidatos") or []:
        if not isinstance(crudo, dict) or not crudo.get("nombre"):
            continue
        try:
            candidatos.append(
                SupplierCandidate(
                    nombre=str(crudo["nombre"]).strip(),
                    precio=crudo.get("precio"),
                    tiempo_envio_estimado=crudo.get("tiempo_envio_estimado"),
                    calificacion=crudo.get("calificacion"),
                    stock_disponible=crudo.get("stock_disponible"),
                    resenas=crudo.get("resenas"),
                    url=crudo.get("url"),
                )
            )
        except Exception:
            # Un candidato mal formado no debe tumbar la lectura de los demás.
            continue

    actual = None
    crudo_actual = datos.get("proveedor_actual")
    if isinstance(crudo_actual, dict) and crudo_actual.get("nombre"):
        try:
            actual = CurrentSupplier(
                nombre=str(crudo_actual["nombre"]).strip(),
                precio=crudo_actual.get("precio") or 0.0,
                tiempo_envio_estimado=crudo_actual.get("tiempo_envio_estimado"),
                calificacion=crudo_actual.get("calificacion"),
                stock_disponible=crudo_actual.get("stock_disponible"),
            )
        except Exception:
            actual = None

    # "¿Es bueno el proveedor?" se lee con toda razón como «mi proveedor», y el
    # modelo lo coloca en proveedor_actual. Pero entonces no queda nadie a quien
    # evaluar y el usuario recibe una petición de datos que ya dio. Si es el
    # único que hay, es el que quiere que miremos: pasa a candidato.
    if not candidatos and actual is not None:
        candidatos = [
            SupplierCandidate(
                nombre=actual.nombre,
                precio=actual.precio or None,
                tiempo_envio_estimado=actual.tiempo_envio_estimado,
                calificacion=actual.calificacion,
                stock_disponible=actual.stock_disponible,
            )
        ]
        actual = None

    if not candidatos:
        return None

    try:
        return ProductInput(
            producto=str(datos.get("producto") or "Producto consultado"),
            categoria=datos.get("categoria"),
            proveedor_actual=actual,
            proveedores_candidatos=candidatos,
        )
    except Exception:
        return None


def extraer(
    texto_usuario: str,
    servicio=None,
    historial: Optional[str] = None,
    texto_anclaje: Optional[str] = None,
) -> Extraccion:
    """
    Lee la conversación y devuelve qué quiere el usuario y con qué datos.

    Con `servicio` disponible usa el modelo, porque la prosa suelta ("tarda
    como dos semanas y me lo dejan en ocho dólares") está fuera del alcance de
    una expresión regular. Sin él, o si la llamada falla, cae a la lectura
    determinista. En ambos casos el resultado pasa por `verificar_anclaje`.

    `texto_anclaje` debe traer solo los turnos del usuario. Anclar contra la
    conversación entera abriría el agujero que esta verificación existe para
    cerrar: el agente pudo haber citado una cifra en un turno anterior, y el
    modelo podría devolverla como si el usuario la hubiera dicho.
    """
    fuente_anclaje = texto_anclaje or texto_usuario

    if servicio is not None and getattr(servicio, "client", None) is not None:
        contexto = (
            f"Conversación previa:\n{historial}\n\n" if historial else ""
        ) + f"Último mensaje del usuario:\n{texto_usuario}"
        datos = servicio.completar_json(PROMPT_EXTRACCION, contexto)

        if datos is not None:
            intencion = str(datos.get("intencion", "")).strip().lower()
            if intencion not in INTENCIONES_VALIDAS:
                intencion = clasificar_deterministico(texto_usuario)

            entrada = _construir_entrada(datos)
            correcciones: List[str] = []
            if entrada is not None:
                entrada, correcciones = verificar_anclaje(entrada, fuente_anclaje)

            faltan = campos_faltantes(entrada)
            if intencion == INTENCION_EVALUACION and faltan:
                intencion = INTENCION_INCOMPLETA
            if entrada is not None and not faltan:
                intencion = INTENCION_EVALUACION

            return Extraccion(
                intencion=intencion,
                entrada=entrada,
                faltantes=faltan if entrada is not None else ["proveedores"],
                correcciones=correcciones,
                fuente="modelo",
            )

    resultado = extraer_deterministico(texto_usuario, texto_completo=fuente_anclaje)
    if resultado.entrada is not None:
        resultado.entrada, resultado.correcciones = verificar_anclaje(
            resultado.entrada, fuente_anclaje
        )
        resultado.faltantes = campos_faltantes(resultado.entrada)
        if resultado.intencion == INTENCION_EVALUACION and resultado.faltantes:
            resultado.intencion = INTENCION_INCOMPLETA
    return resultado
