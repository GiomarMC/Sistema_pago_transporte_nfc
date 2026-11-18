// Adaptador entre las páginas del portal (M14) y el cliente del contrato C4 (api.js).
//
// Las páginas hablan con `api` usando objetos con nombres de formulario ({dni, clave, ...});
// aquí se traducen a las funciones de api.js / api-simulada.js, que son las que conocen las
// rutas y los cuerpos de C4. No se duplica ninguna ruta, salvo la extensión reenviarOtp.

import { cargarApi, esSimulado, ErrorApi } from "./api.js";

export const simulado = esSimulado();
const c4 = await cargarApi();

// Las páginas leen exc.reintentar_en (como lo manda el servidor); ErrorApi lo guarda como
// reintentarEn.
async function conCompat(promesa) {
  try {
    return await promesa;
  } catch (exc) {
    if (exc instanceof ErrorApi && exc.reintentar_en === undefined) exc.reintentar_en = exc.reintentarEn;
    throw exc;
  }
}

const leerCookie = (nombre) => {
  for (const parte of (globalThis.document?.cookie ?? "").split(";")) {
    const [k, ...v] = parte.trim().split("=");
    if (k === nombre) return decodeURIComponent(v.join("="));
  }
  return "";
};

// Extensión de C4 pendiente de M12: reenviar el código OTP de un inicio de sesión. En modo
// simulado no hace nada; con el servidor real usa POST /api/v1/sesion/otp/reenviar/.
async function reenviarOtp() {
  if (simulado) return null;
  let resp;
  try {
    resp = await fetch("/api/v1/sesion/otp/reenviar/", {
      method: "POST", credentials: "same-origin",
      headers: { Accept: "application/json", "X-CSRFToken": leerCookie("csrftoken") },
    });
  } catch {
    throw new ErrorApi("red", "No hay conexión. Revisa tu internet e intenta de nuevo.");
  }
  if (resp.ok) return null;
  const e = (await resp.json().catch(() => ({})))?.error;
  throw new ErrorApi(e?.codigo ?? "servidor", e?.mensaje ?? "No se pudo reenviar el código.",
                     e?.campos ?? {}, resp.status, e?.reintentar_en ?? null);
}

// Hay sesión si C4 responde el resumen; no_autenticado significa "no".
async function sesion() {
  try {
    await c4.resumen();
    return true;
  } catch (exc) {
    if (exc.codigo === "no_autenticado") return false;
    throw exc;
  }
}

const traducciones = {
  sesion,
  ingresar: ({ dni, clave }) => c4.ingresar(dni, clave),
  confirmarOtp: ({ codigo, recordar }) => c4.confirmarOtp(codigo, recordar),
  reenviarOtp,
  salir: () => c4.salir(),
  activarIdentidad: ({ dni, tarjeta, codigo }) => c4.activarTarjeta(dni, tarjeta, codigo),
  activarCorreo: ({ token, correo }) => c4.activacionCorreo(token, correo),
  activarConfirmar: ({ token, codigo, clave }) => c4.activacionConfirmar(token, codigo, clave),
  recuperar: ({ dni }) => c4.recuperarContrasena(dni),
  restablecer: ({ uid, token, clave }) => c4.restablecerContrasena(uid, token, clave),
  cambiarClave: ({ actual, nueva }) => c4.cambiarContrasena(actual, nueva),
  resumen: () => c4.resumen(),
  movimientos: (filtros) => c4.movimientos(filtros),
  crearOrden: ({ monto, idempotencia }) => c4.crearOrden(monto, idempotencia),
  orden: (id) => c4.orden(id),
  bloquear: ({ id, motivo, clave }) => c4.bloquearTarjeta(id, motivo, clave),
  reposiciones: () => c4.reposiciones(),
  solicitarReposicion: ({ tarjeta_id: tarjetaId }) => c4.solicitarReposicion(tarjetaId),
  accesos: () => c4.accesos(),
  dispositivos: () => c4.dispositivos(),
  quitarDispositivo: ({ id }) => c4.olvidarDispositivo(id),
  perfil: () => c4.perfil(),
  pedirCambioCorreo: ({ correo }) => c4.cambiarCorreo(correo),
  confirmarCorreo: ({ correo, codigo }) => c4.confirmarCorreo(correo, codigo),
};

export const api = Object.fromEntries(Object.entries(traducciones).map(
  ([nombre, f]) => [nombre, (...args) => conCompat(Promise.resolve().then(() => f(...args)))]));
