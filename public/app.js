/**
 * FluentDrop — interfaz de la sala de despacho
 * ============================================
 *
 * Un chat contra `/api/chat`. Lo que distingue a esta interfaz de cualquier
 * otra es lo que pinta debajo de cada respuesta: la tira de aduanas por las
 * que pasó el caso, con su latencia real, y el desglose MCDA de cada
 * proveedor evaluado.
 *
 * Sobre el sellado de las aduanas: el backend corre como función serverless y
 * devuelve el informe completo de una vez, no por streaming. Las latencias de
 * cada aduana son reales —las mide el pipeline— pero el sellado en secuencia
 * se reproduce aquí al recibir la respuesta, no en vivo. Se reproduce a
 * escala, conservando las proporciones: así se sigue viendo de un vistazo que
 * los gates deterministas tardan milisegundos y que la espera larga es
 * siempre el modelo.
 */

const API = "";

const hilo = document.getElementById("hilo");
const bienvenida = document.getElementById("bienvenida");
const formulario = document.getElementById("formulario");
const entrada = document.getElementById("entrada");
const botonEnviar = document.getElementById("enviar");
const anuncio = document.getElementById("anuncio");
const motor = document.getElementById("motor");
const motorTexto = document.getElementById("motor-texto");
const casosCaja = document.getElementById("casos");
const casosLista = document.getElementById("casos-lista");

/** La conversación completa. Se envía entera: el backend no guarda estado. */
const mensajes = [];
let ocupado = false;
let interior = null;

// Etiquetas de espera. Siguen las etapas reales del pipeline, en su orden.
const ETAPAS = [
  "Leyendo",
  "Pre-vuelo",
  "Integridad",
  "Filtros duros",
  "Triaje",
  "Contrato",
  "Despacho",
];

// ─── Utilidades ─────────────────────────────────────────────────────────────

/** Escapa antes de insertar. Todo lo demás se construye sobre esto. */
function escapar(texto) {
  return String(texto)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/**
 * Formato mínimo: negritas y enlaces.
 *
 * El orden importa y no es intercambiable: se escapa primero y se marca
 * después. Al revés, una respuesta que contuviera `<script>` se insertaría
 * como etiqueta viva.
 */
function formatear(texto) {
  return escapar(texto)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(
      /(https?:\/\/[^\s<]+)/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>',
    );
}

function parrafos(texto) {
  return texto
    .split(/\n{2,}/)
    .map((bloque) => bloque.trim())
    .filter(Boolean)
    .map((bloque) => `<p>${formatear(bloque)}</p>`)
    .join("");
}

function crear(etiqueta, clase, html) {
  const nodo = document.createElement(etiqueta);
  if (clase) nodo.className = clase;
  if (html !== undefined) nodo.innerHTML = html;
  return nodo;
}

function alFinal() {
  hilo.scrollTo({ top: hilo.scrollHeight, behavior: "smooth" });
}

/** El contenedor del hilo se crea al primer mensaje, reemplazando la bienvenida. */
function contenedor() {
  if (!interior) {
    bienvenida.remove();
    interior = crear("div", "hilo-interior");
    hilo.appendChild(interior);
  }
  return interior;
}

function numero(valor, sufijo = "") {
  return valor === null || valor === undefined ? null : `${valor}${sufijo}`;
}

function dinero(valor) {
  return valor === null || valor === undefined
    ? null
    : `$${Number(valor).toFixed(2)}`;
}

// ─── Pintado ────────────────────────────────────────────────────────────────

function pintarUsuario(texto) {
  const nodo = crear("div", "turno turno-usuario");
  nodo.textContent = texto;
  contenedor().appendChild(nodo);
  alFinal();
}

/** Indicador de espera con las etapas rotando mientras el agente trabaja. */
function pintarEspera() {
  const nodo = crear(
    "div",
    "turno espera",
    '<span class="espera-orbe"></span><span class="espera-etiqueta">Leyendo</span>',
  );
  contenedor().appendChild(nodo);
  alFinal();

  let indice = 0;
  const reloj = setInterval(() => {
    indice = (indice + 1) % ETAPAS.length;
    // Hay que volver a buscar la etiqueta en cada vuelta. Guardar la
    // referencia de fuera la deja obsoleta en cuanto se reemplaza la primera
    // vez, y `replaceWith` sobre un nodo ya desprendido no hace nada: el
    // rotulo se quedaria congelado en la segunda etapa.
    const viva = nodo.querySelector(".espera-etiqueta");
    const nueva = crear("span", "espera-etiqueta");
    nueva.textContent = ETAPAS[indice];
    viva.replaceWith(nueva);
  }, 900);

  return () => {
    clearInterval(reloj);
    nodo.remove();
  };
}

/**
 * La tira de aduanas.
 *
 * Los sellos aparecen en orden. El retardo de cada uno es proporcional a su
 * latencia real, reescalado para que la secuencia completa dure alrededor de
 * un segundo y con un piso para que ningún sello pase desapercibido.
 */
function pintarAduanas(traza) {
  const tira = crear("div", "aduanas");
  if (!traza || traza.length === 0) return tira;

  const total = traza.reduce((suma, paso) => suma + (paso.latencia_ms || 0), 0);
  const escala = total > 0 ? 950 / total : 0;
  let acumulado = 0;

  traza.forEach((paso) => {
    const sello = crear("span", "sello");
    sello.dataset.estado = paso.passed ? "ok" : "bloqueado";
    sello.tabIndex = 0;

    const ms = paso.latencia_ms || 0;
    sello.innerHTML =
      `${escapar(paso.id)}` +
      (ms >= 1 ? ` <span class="sello-ms">${Math.round(ms)}ms</span>` : "") +
      `<span class="sello-globo"><b>${escapar(paso.nombre)}</b>${escapar(paso.motivo || "")}</span>`;

    tira.appendChild(sello);

    acumulado += Math.max(70, ms * escala);
    setTimeout(() => sello.setAttribute("data-visible", ""), acumulado);
  });

  return tira;
}

/** El desglose MCDA: la evidencia con la que se puede auditar el veredicto. */
function pintarEvidencia(evidencia) {
  const bloque = crear("details", "evidencia");
  const cuantos = evidencia.length;
  bloque.appendChild(
    crear(
      "summary",
      null,
      `Evidencia · ${cuantos} proveedor${cuantos === 1 ? "" : "es"} evaluado${cuantos === 1 ? "" : "s"}`,
    ),
  );

  evidencia.forEach((item) => {
    const fila = crear("div", "proveedor");
    fila.dataset.puesto = String(item.rank);
    fila.dataset.viable = item.viable ? "si" : "no";

    fila.appendChild(crear("div", "proveedor-puesto", String(item.rank)));
    fila.appendChild(crear("div", "proveedor-nombre", escapar(item.nombre)));

    const datos = [
      dinero(item.precio),
      item.envio ? escapar(item.envio) : null,
      numero(item.calificacion, "/5"),
      numero(item.stock, " u."),
      `MCDA ${Number(item.score_final).toFixed(3)}`,
    ].filter(Boolean);
    fila.appendChild(
      crear("div", "proveedor-datos", datos.map((d) => `<span>${d}</span>`).join("")),
    );

    if (!item.viable && item.motivo_exclusion) {
      fila.appendChild(
        crear("div", "proveedor-excluido", `Excluido: ${escapar(item.motivo_exclusion)}`),
      );
    } else {
      const sub = item.subpuntajes || {};
      const barras = crear("div", "barras");
      [
        ["Precio", sub.precio],
        ["Envío", sub.envio],
        ["Calificación", sub.calificacion],
        ["Stock", sub.stock],
      ].forEach(([nombre, valor]) => {
        const v = Number(valor || 0);
        barras.appendChild(
          crear(
            "div",
            "barra",
            `<span class="barra-nombre">${nombre}</span>` +
              `<span class="barra-valor">${v.toFixed(2)}</span>` +
              `<span class="barra-riel"><span class="barra-relleno" style="width:${Math.round(v * 100)}%"></span></span>`,
          ),
        );
      });
      fila.appendChild(barras);
    }

    if (item.motivo_riesgo) {
      fila.appendChild(crear("div", "riesgo", escapar(item.motivo_riesgo)));
    }

    bloque.appendChild(fila);
  });

  return bloque;
}

function pintarAgente(turno) {
  const nodo = crear("div", "turno turno-agente");
  nodo.dataset.tipo = turno.tipo;
  nodo.dataset.revision = turno.requiere_revision ? "si" : "no";

  nodo.appendChild(
    crear("div", "agente-firma", '<span class="firma-punto"></span>FluentDrop'),
  );
  nodo.appendChild(crear("div", "cuerpo", parrafos(turno.respuesta)));

  if (turno.traza && turno.traza.length) {
    const tira = pintarAduanas(turno.traza);
    if (turno.duracion_ms) {
      tira.appendChild(
        crear("span", "aduanas-total", `${(turno.duracion_ms / 1000).toFixed(1)}s`),
      );
    }
    nodo.appendChild(tira);
  }

  if (turno.evidencia && turno.evidencia.length) {
    nodo.appendChild(pintarEvidencia(turno.evidencia));
  }

  contenedor().appendChild(nodo);
  alFinal();
  anuncio.textContent = turno.respuesta;
}

function pintarError(mensaje) {
  const nodo = crear("div", "turno error");
  nodo.textContent = mensaje;
  contenedor().appendChild(nodo);
  alFinal();
  anuncio.textContent = mensaje;
}

// ─── Envío ──────────────────────────────────────────────────────────────────

function ajustarAlto() {
  entrada.style.height = "auto";
  entrada.style.height = `${Math.min(entrada.scrollHeight, 168)}px`;
  botonEnviar.toggleAttribute("data-listo", entrada.value.trim().length > 0 && !ocupado);
}

async function enviar(texto) {
  if (ocupado || !texto.trim()) return;

  ocupado = true;
  botonEnviar.disabled = true;
  botonEnviar.removeAttribute("data-listo");
  entrada.value = "";
  ajustarAlto();

  mensajes.push({ rol: "usuario", contenido: texto });
  pintarUsuario(texto);
  const quitarEspera = pintarEspera();

  try {
    const respuesta = await fetch(`${API}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mensajes }),
    });

    quitarEspera();

    if (!respuesta.ok) {
      let detalle = `El servidor respondió ${respuesta.status}.`;
      try {
        const cuerpo = await respuesta.json();
        if (cuerpo && cuerpo.error) detalle = cuerpo.error;
      } catch {
        // El cuerpo no era JSON; se queda el mensaje por código de estado.
      }
      pintarError(`No pude procesar la consulta. ${detalle}`);
      return;
    }

    const turno = await respuesta.json();
    mensajes.push({ rol: "agente", contenido: turno.respuesta });
    pintarAgente(turno);
    marcarMotor(turno.modelo);
  } catch {
    quitarEspera();
    pintarError(
      "No pude comunicarme con el servidor. Revisa tu conexión y vuelve a intentarlo.",
    );
  } finally {
    ocupado = false;
    botonEnviar.disabled = false;
    ajustarAlto();
    entrada.focus();
  }
}

// ─── Estado del motor ───────────────────────────────────────────────────────

/** Dice con qué motor se atendió el último turno, sin adornos. */
function marcarMotor(modelo) {
  if (!modelo) return;
  motor.hidden = false;

  if (modelo.startsWith("deterministic") || modelo.startsWith("fallback") || modelo === "short_circuit_zero_llm") {
    motor.dataset.estado = modelo.includes("error") ? "caido" : "determinista";
    motorTexto.textContent =
      modelo === "short_circuit_zero_llm" ? "cortocircuito · sin LLM" : "motor determinista";
  } else {
    motor.dataset.estado = "modelo";
    motorTexto.textContent = modelo.split("/").pop();
  }
}

async function consultarSalud() {
  try {
    const r = await fetch(`${API}/api/salud`);
    if (!r.ok) throw new Error();
    const salud = await r.json();
    motor.hidden = false;
    motor.dataset.estado = salud.modelo_disponible ? "modelo" : "determinista";
    motorTexto.textContent = salud.modelo_disponible
      ? salud.modelo.split("/").pop()
      : "motor determinista";
  } catch {
    motor.hidden = false;
    motor.dataset.estado = "caido";
    motorTexto.textContent = "sin backend";
  }
}

// ─── Casos de demostración ──────────────────────────────────────────────────

async function cargarCasos() {
  try {
    const r = await fetch(`${API}/api/casos`);
    if (!r.ok) return;
    const casos = await r.json();
    if (!Array.isArray(casos) || casos.length === 0) return;

    casos.forEach((caso) => {
      const chip = crear("button", "caso");
      chip.type = "button";
      chip.textContent = caso.titulo;
      chip.title = caso.nota || "";
      chip.addEventListener("click", () => {
        if (ocupado) return;
        enviar(caso.contenido);
      });
      casosLista.appendChild(chip);
    });
    casosCaja.hidden = false;
  } catch {
    // Sin casos la interfaz funciona igual; no hay nada que avisar.
  }
}

// ─── Enlaces ────────────────────────────────────────────────────────────────

formulario.addEventListener("submit", (evento) => {
  evento.preventDefault();
  enviar(entrada.value);
});

entrada.addEventListener("input", ajustarAlto);

entrada.addEventListener("keydown", (evento) => {
  // Enter envía; Mayús+Enter abre línea. Es lo que ya espera cualquiera que
  // haya usado un chat.
  if (evento.key === "Enter" && !evento.shiftKey) {
    evento.preventDefault();
    enviar(entrada.value);
  }
});

document.querySelectorAll("[data-ejemplo]").forEach((boton) => {
  boton.addEventListener("click", () => enviar(boton.textContent.trim()));
});

ajustarAlto();
entrada.focus();
consultarSalud();
cargarCasos();
