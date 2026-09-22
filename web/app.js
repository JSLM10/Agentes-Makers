/* FluentDrop — lee el stream NDJSON del pipeline y estampa cada aduana al resolver. */

const GATES = [
  ["G0", "Pre-vuelo y credenciales"],
  ["G1", "Integridad del manifiesto"],
  ["G2", "Filtros duros de negocio"],
  ["G3", "Triaje de viabilidad"],
  ["G4", "Contrato y guardarraíles"],
  ["G5", "Despacho e idempotencia"],
];

const CASO_INICIAL = `Producto en tienda: Audifonos inalambricos Bluetooth TWS
Proveedor actual: Proveedor Actual - $19.00 USD, envio 25 dias, calificacion 3.7/5, stock 8 unidades
Proveedores candidatos encontrados:
- Proveedor A6S: $3.71 USD, envio 18-25 dias, calificacion 4.3/5, stock 500 unidades, url https://es.aliexpress.com/item/1005008070148600.html
- Proveedor TWS9D: $15.68 USD, envio 8-12 dias, calificacion 4.7/5, stock 300 unidades, url https://es.aliexpress.com/item/1005007010288547.html
- Proveedor MiniSlim: $17.23 USD, envio 6-10 dias, calificacion 4.8/5, stock 180 unidades, url https://es.aliexpress.com/item/1005011653931360.html`;

const $ = (id) => document.getElementById(id);
const manifiesto = $("manifiesto");
const boton = $("despachar");
const linea = $("linea");
const cajaError = $("error");
const panelDespacho = $("panel-despacho");

manifiesto.value = CASO_INICIAL;

/** Dibuja la tubería en reposo: seis estaciones y la del modelo entre G3 y G4. */
function reiniciarLinea() {
  linea.textContent = "";
  for (const [id, nombre] of GATES) {
    if (id === "G4") linea.append(crearEstacion("SN", "Síntesis con el modelo", true));
    linea.append(crearEstacion(id, nombre, false));
  }
}

function crearEstacion(id, nombre, esSintesis) {
  const li = document.createElement("li");
  li.className = "estacion" + (esSintesis ? " estacion-sintesis" : "");
  li.dataset.estado = "pendiente";
  li.dataset.gate = id;

  const sello = document.createElement("span");
  sello.className = "sello";
  sello.textContent = esSintesis ? "~" : "·";
  sello.setAttribute("aria-hidden", "true");

  const titulo = document.createElement("span");
  titulo.className = "estacion-titulo";
  const codigo = document.createElement("span");
  codigo.className = "estacion-codigo";
  codigo.textContent = esSintesis ? "LLM" : id;
  titulo.append(codigo, document.createTextNode(nombre));

  const ms = document.createElement("span");
  ms.className = "estacion-ms";

  const motivo = document.createElement("span");
  motivo.className = "estacion-motivo";
  motivo.textContent = esSintesis ? "Solo se invoca si quedaron candidatos viables." : "";

  li.append(sello, titulo, ms, motivo);
  return li;
}

// Tiempo acumulado de cada aduana, para poder aislar cuánto tardó el modelo.
let msPorGate = {};

function estampar(evento) {
  const li = linea.querySelector(`[data-gate="${evento.id}"]`);
  if (!li) return;
  msPorGate[evento.id] = evento.ms;

  li.dataset.estado = evento.aprobado ? "despachado" : "bloqueado";
  li.querySelector(".sello").textContent = evento.aprobado ? "✓" : "✕";
  li.querySelector(".estacion-ms").textContent = `${evento.ms} ms`;
  li.querySelector(".estacion-motivo").textContent = evento.motivo || "";

  // Apenas G3 resuelve, el modelo pasa a trabajar: es la espera visible del pipeline.
  const sintesis = linea.querySelector('[data-gate="SN"]');
  if (evento.id === "G3") {
    if (evento.aprobado) {
      sintesis.dataset.estado = "trabajando";
      sintesis.querySelector(".estacion-motivo").textContent = "Redactando la recomendación…";
    } else {
      sintesis.querySelector(".sello").textContent = "—";
      sintesis.querySelector(".estacion-motivo").textContent =
        "Omitido: sin candidatos viables, el modelo no se llama. Costo cero.";
    }
  }
  // El modelo termina justo antes de G4: su costo es la diferencia con G3.
  if (evento.id === "G4" && sintesis.dataset.estado === "trabajando") {
    const msModelo = Math.max(0, evento.ms - (msPorGate.G3 ?? evento.ms));
    sintesis.dataset.estado = "despachado";
    sintesis.querySelector(".sello").textContent = "~";
    sintesis.querySelector(".estacion-ms").textContent = `${msModelo.toFixed(1)} ms`;
    sintesis.querySelector(".estacion-motivo").textContent =
      "Redactó la recomendación. Todo lo demás de esta columna es código determinista.";
  }
}

function fila(etiqueta, valor) {
  const tr = document.createElement("tr");
  const th = document.createElement("th");
  th.scope = "row";
  th.textContent = etiqueta;
  const td = document.createElement("td");
  td.textContent = valor;
  tr.append(th, td);
  return tr;
}

function pintarDespacho(evento) {
  const s = evento.salida;
  const caja = $("despacho");
  caja.textContent = "";

  if (s.proveedor_recomendado) {
    const veredicto = document.createElement("div");
    veredicto.className = "veredicto";
    const nombre = document.createElement("span");
    nombre.className = "veredicto-proveedor";
    nombre.textContent = s.proveedor_recomendado;
    const precio = document.createElement("span");
    precio.className = "veredicto-precio";
    precio.textContent = s.precio == null ? "sin precio" : `$${s.precio}`;
    veredicto.append(nombre, precio);
    caja.append(veredicto);
  } else {
    const vacio = document.createElement("p");
    vacio.className = "veredicto-vacio";
    vacio.textContent = "Ningún proveedor califica";
    caja.append(vacio);
  }

  const tabla = document.createElement("table");
  tabla.className = "ficha";
  const cuerpo = document.createElement("tbody");
  cuerpo.append(
    fila("Producto", s.producto ?? "sin identificar"),
    fila("Tiempo de envío", s.tiempo_envio_estimado ?? "sin dato"),
    fila("Calificación", s.calificacion == null ? "sin dato" : `${s.calificacion} / 5`),
    fila("Stock disponible", s.stock_disponible == null ? "sin dato" : `${s.stock_disponible} unidades`),
    fila("Motor que redactó", evento.motor),
    fila("Tiempo total", `${evento.ms} ms`),
  );
  tabla.append(cuerpo);
  caja.append(tabla);

  if (s.url_producto) {
    const a = document.createElement("a");
    a.className = "enlace-producto";
    a.href = s.url_producto;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    const rotulo = document.createElement("strong");
    rotulo.textContent = "Abrir en el catálogo";
    const destino = document.createElement("span");
    destino.className = "enlace-destino";
    destino.textContent = s.url_producto;
    a.append(rotulo, destino);
    caja.append(a);
  } else if (s.proveedor_recomendado) {
    const p = document.createElement("p");
    p.className = "sin-enlace";
    p.textContent = "Sin enlace: el proveedor recomendado no traía uno en la declaración.";
    caja.append(p);
  }

  if (s.requiere_revision_humana) {
    const aviso = document.createElement("p");
    aviso.className = "aviso-revision";
    aviso.textContent = "Requiere revisión humana antes de actuar.";
    caja.append(aviso);
  }

  const alternativas = (s.alternativas || []).filter((a) => a && a.nombre);
  if (alternativas.length) {
    const bloque = document.createElement("div");
    bloque.className = "alternativas";
    const h3 = document.createElement("h3");
    h3.textContent = "Otros proveedores evaluados";
    const tabla2 = document.createElement("table");
    tabla2.className = "ficha";
    const cuerpo2 = document.createElement("tbody");
    for (const alt of alternativas) {
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      th.scope = "row";
      th.textContent = alt.nombre;

      const precio = document.createElement("td");
      precio.className = "col-precio";
      precio.textContent = alt.precio == null ? "—" : `$${alt.precio}`;

      const calif = document.createElement("td");
      calif.className = "col-calif";
      calif.textContent = alt.calificacion == null ? "—" : `${alt.calificacion} / 5`;

      const enlace = document.createElement("td");
      if (alt.url) {
        const a = document.createElement("a");
        a.href = alt.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = "ver producto";
        enlace.append(a);
      } else {
        enlace.textContent = "sin enlace";
      }

      tr.append(th, precio, calif, enlace);
      cuerpo2.append(tr);
    }
    tabla2.append(cuerpo2);
    bloque.append(h3, tabla2);
    caja.append(bloque);
  }

  $("contrato-json").textContent = JSON.stringify(s, null, 2);
  $("folio").textContent = evento.ejecucion;
  $("motor").textContent = evento.motor;
  panelDespacho.hidden = false;
}

async function despachar() {
  const entrada = manifiesto.value.trim();
  if (!entrada) {
    mostrarError("Escribe al menos un producto y un proveedor antes de despachar.");
    return;
  }

  cajaError.hidden = true;
  panelDespacho.hidden = true;
  boton.disabled = true;
  boton.textContent = "Despachando…";
  msPorGate = {};
  $("folio").textContent = "—";
  $("motor").textContent = "en curso";
  reiniciarLinea();

  const offline = document.querySelector('input[name="motor"]:checked').value === "offline";

  try {
    const respuesta = await fetch("/api/despachar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entrada, offline }),
    });

    const lector = respuesta.body.getReader();
    const decodificador = new TextDecoder();
    let resto = "";

    // Cada línea completa que llega se pinta de inmediato: eso es el vivo.
    for (;;) {
      const { done, value } = await lector.read();
      if (done) break;
      resto += decodificador.decode(value, { stream: true });
      const lineas = resto.split("\n");
      resto = lineas.pop();
      for (const cruda of lineas) {
        if (!cruda.trim()) continue;
        const evento = JSON.parse(cruda);
        if (evento.tipo === "gate") estampar(evento);
        else if (evento.tipo === "resultado") pintarDespacho(evento);
        else if (evento.tipo === "error") mostrarError(evento.mensaje);
      }
    }
  } catch (err) {
    mostrarError(`No se pudo completar el despacho: ${err.message}`);
  } finally {
    boton.disabled = false;
    boton.textContent = "Despachar manifiesto";
  }
}

function mostrarError(mensaje) {
  cajaError.textContent = mensaje;
  cajaError.hidden = false;
  $("motor").textContent = "detenido";
}

boton.addEventListener("click", despachar);
reiniciarLinea();
