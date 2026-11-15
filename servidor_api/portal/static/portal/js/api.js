// Cliente JS del contrato C4 (API del portal del cliente).
//
// Fuente del contrato: docs/contratos/api-cliente-v1.yaml. Hay una función por
// cada ruta; api-simulada.js exporta exactamente las mismas, con las mismas
// firmas, y responde con datos falsos sin servidor.
//
// Las páginas no importan este archivo directamente, sino así:
//
//   import { cargarApi } from "./api.js";
//   const api = await cargarApi();   // api.js o api-simulada.js
//   const datos = await api.resumen();
//
// cargarApi() elige la versión simulada con ?simulado=1 (queda guardado en
// localStorage.simulado = "1" para las páginas siguientes; ?simulado=0 lo quita).
//
// Todas las funciones devuelven una promesa con el cuerpo JSON de la respuesta
// (o null en las respuestas 202/204) y, si algo falla, la rechazan con un
// ErrorApi. Además de los códigos de C4 hay dos que solo genera el cliente:
// "red" (no se pudo hablar con el servidor) y "servidor" (respuesta 5xx o que
// no es JSON).

export const BASE = "/api/v1/";

export class ErrorApi extends Error {
  constructor(codigo, mensaje, campos = {}, estado = 0, reintentarEn = null) {
    super(mensaje);
    this.name = "ErrorApi";
    this.codigo = codigo;
    this.mensaje = mensaje;
    this.campos = campos;
    this.estado = estado;
    this.reintentarEn = reintentarEn;
  }
}

// ------------------------------------------------------------- Selección de modo

export function esSimulado() {
  const params = new URLSearchParams(globalThis.location?.search ?? "");
  const almacen = globalThis.localStorage;
  if (params.get("simulado") === "1") almacen?.setItem("simulado", "1");
  if (params.get("simulado") === "0") almacen?.removeItem("simulado");
  return params.get("simulado") === "1" || almacen?.getItem("simulado") === "1";
}

export function cargarApi() {
  return esSimulado() ? import("./api-simulada.js") : import("./api.js");
}

// Clave para Idempotency-Key: se genera una vez por intento de compra y se
// reutiliza en los reintentos de ese intento.
export function nuevaClave() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const b = new Uint8Array(16);
  globalThis.crypto.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

// ------------------------------------------------------------- Transporte

const POR_ESTADO = {
  401: ["no_autenticado", "Inicia sesión para continuar."],
  403: ["prohibido", "No tienes permiso para hacer esto."],
  404: ["no_encontrado", "No encontramos lo que buscas."],
  429: ["bloqueo_temporal", "Demasiados intentos. Espera unos minutos."],
};

function leerCookie(nombre) {
  const texto = globalThis.document?.cookie ?? "";
  for (const parte of texto.split(";")) {
    const [k, ...v] = parte.trim().split("=");
    if (k === nombre) return decodeURIComponent(v.join("="));
  }
  return "";
}

function errorPorEstado(estado) {
  const [codigo, mensaje] = POR_ESTADO[estado] ??
    ["servidor", "El servicio no está disponible. Intenta de nuevo en unos minutos."];
  return new ErrorApi(codigo, mensaje, {}, estado);
}

async function pedir(metodo, ruta, { cuerpo, consulta, cabeceras } = {}) {
  let url = BASE + ruta;
  if (consulta) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(consulta)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    }
    if ([...qs].length) url += "?" + qs;
  }
  const headers = { Accept: "application/json", ...cabeceras };
  if (metodo !== "GET") headers["X-CSRFToken"] = leerCookie("csrftoken");
  if (cuerpo !== undefined) headers["Content-Type"] = "application/json";

  let resp;
  try {
    resp = await fetch(url, {
      method: metodo,
      credentials: "same-origin",
      headers,
      body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
    });
  } catch {
    throw new ErrorApi("red", "No hay conexión. Revisa tu internet e intenta de nuevo.");
  }
  if (resp.status === 202 || resp.status === 204) return null;

  let datos;
  try {
    datos = await resp.json();
  } catch {
    if (resp.ok) return null;
    throw errorPorEstado(resp.status);
  }
  if (resp.ok) return datos;
  const e = datos?.error;
  if (!e?.codigo) throw errorPorEstado(resp.status);
  throw new ErrorApi(e.codigo, e.mensaje ?? "", e.campos ?? {}, resp.status,
                     e.reintentar_en ?? null);
}

const id_ = (v) => encodeURIComponent(String(v));

// ------------------------------------------------------------- Activación

export function activarTarjeta(documento, numeroTarjeta, codigo) {
  return pedir("POST", "activacion/", {
    cuerpo: { documento, numero_tarjeta: numeroTarjeta, codigo },
  });
}

export function activacionCorreo(tokenActivacion, correo) {
  return pedir("POST", "activacion/correo/", {
    cuerpo: { token_activacion: tokenActivacion, correo },
  });
}

export function activacionConfirmar(tokenActivacion, otp, contrasena) {
  return pedir("POST", "activacion/confirmar/", {
    cuerpo: { token_activacion: tokenActivacion, otp, contrasena },
  });
}

// ------------------------------------------------------------- Sesión

export function ingresar(documento, contrasena) {
  return pedir("POST", "sesion/", { cuerpo: { documento, contrasena } });
}

export function confirmarOtp(otp, recordarDispositivo = false) {
  return pedir("POST", "sesion/otp/", {
    cuerpo: { otp, recordar_dispositivo: Boolean(recordarDispositivo) },
  });
}

export function salir() {
  return pedir("DELETE", "sesion/");
}

// ------------------------------------------------------------- Contraseña

export function recuperarContrasena(documento) {
  return pedir("POST", "contrasena/recuperar/", { cuerpo: { documento } });
}

export function restablecerContrasena(uid, token, contrasena) {
  return pedir("POST", "contrasena/restablecer/", { cuerpo: { uid, token, contrasena } });
}

export function cambiarContrasena(actual, nueva) {
  return pedir("POST", "contrasena/cambiar/", { cuerpo: { actual, nueva } });
}

// ------------------------------------------------------------- Consulta

export function resumen() {
  return pedir("GET", "resumen/");
}

// filtros: { desde, hasta, tipo, pagina }; los vacíos no se envían.
export function movimientos(filtros = {}) {
  const { desde, hasta, tipo, pagina } = filtros;
  return pedir("GET", "movimientos/", { consulta: { desde, hasta, tipo, pagina } });
}

export function perfil() {
  return pedir("GET", "perfil/");
}

export function cambiarCorreo(correo) {
  return pedir("POST", "perfil/correo/", { cuerpo: { correo } });
}

export function confirmarCorreo(correo, otp) {
  return pedir("POST", "perfil/correo/confirmar/", { cuerpo: { correo, otp } });
}

export function accesos() {
  return pedir("GET", "seguridad/accesos/");
}

export function dispositivos() {
  return pedir("GET", "seguridad/dispositivos/");
}

export function olvidarDispositivo(id) {
  return pedir("DELETE", `seguridad/dispositivos/${id_(id)}/`);
}

// ------------------------------------------------------------- Tarjeta

export function bloquearTarjeta(id, motivo, contrasena) {
  return pedir("POST", `tarjetas/${id_(id)}/bloquear/`, { cuerpo: { motivo, contrasena } });
}

export function reposiciones() {
  return pedir("GET", "reposiciones/");
}

export function solicitarReposicion(tarjetaId) {
  return pedir("POST", "reposiciones/", { cuerpo: { tarjeta_id: tarjetaId } });
}

// ------------------------------------------------------------- Recargas

export function ordenes() {
  return pedir("GET", "recargas/ordenes/");
}

export function orden(id) {
  return pedir("GET", `recargas/ordenes/${id_(id)}/`);
}

// clave: Idempotency-Key. Pasa la misma en los reintentos del mismo intento.
export function crearOrden(monto, clave = nuevaClave()) {
  return pedir("POST", "recargas/ordenes/", {
    cuerpo: { monto },
    cabeceras: { "Idempotency-Key": clave },
  });
}
