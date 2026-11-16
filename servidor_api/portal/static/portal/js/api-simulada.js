// Versión simulada del cliente C4: las mismas funciones y firmas que api.js,
// pero sin servidor. Sirve para construir el portal (M14) antes que el backend.
//
// Se activa con ?simulado=1 (o localStorage.simulado = "1"); ver cargarApi()
// en api.js. Cada respuesta tarda 300 ms.
//
// Datos: la clienta "Ana Quispe" (DNI 45678912), saldo S/ 12.40, tarifa General
// (S/ 1.00), 45 movimientos y una tarjeta activa. El estado se guarda en
// sessionStorage, así que se mantiene al cambiar de página (bloquear la
// tarjeta, recargar, etc.). Para empezar de cero:
//   sessionStorage.removeItem("api-simulada")
//
// Valores de prueba:
//   - contraseña: "subepe123"; OTP (sesión, activación, correo): "123456"
//   - la sesión empieza iniciada; tras salir() hay que volver a ingresar
//   - recargas: la orden se paga a los 2 s y se acredita a los 4 s, salvo
//       S/ 6.66 → fallida, S/ 7.77 → expirada, S/ 8.88 → se queda pendiente
//   - ?error=<codigo>: toda función que según C4 puede devolver ese código lo
//     devuelve (con su estado HTTP). "red" y "servidor" valen para todas.
//     Con bloqueo_temporal, ?reintentar_en=<s> cambia la espera (por defecto 90).

import { ErrorApi, nuevaClave } from "./api.js";

export { BASE, ErrorApi, esSimulado, cargarApi, nuevaClave } from "./api.js";

const RETARDO = 300;
const CLAVE_ESTADO = "api-simulada";
const CONTRASENA = "subepe123";
const OTP = "123456";
const DOCUMENTO = "45678912";

// ------------------------------------------------------------- Errores

const ESTADO_HTTP = {
  no_autenticado: 401, prohibido: 403, no_encontrado: 404, validacion: 400,
  credenciales_invalidas: 401, bloqueo_temporal: 429, otp_invalido: 400,
  otp_expirado: 400, activacion_invalida: 400, monto_fuera_de_limite: 400,
  limite_diario: 400, idempotencia_conflicto: 409, tarjeta_no_activa: 409,
  red: 0, servidor: 500,
};

const MENSAJES = {
  no_autenticado: "Inicia sesión para continuar.",
  prohibido: "No tienes permiso para hacer esto.",
  no_encontrado: "No encontramos lo que buscas.",
  validacion: "Revisa los datos marcados.",
  credenciales_invalidas: "DNI o contraseña incorrectos.",
  bloqueo_temporal: "Demasiados intentos. Espera unos minutos.",
  otp_invalido: "El código no es correcto.",
  otp_expirado: "El código venció. Pide uno nuevo.",
  activacion_invalida: "Los datos no coinciden con ninguna tarjeta por activar.",
  monto_fuera_de_limite: "El monto debe estar entre S/ 1.00 y S/ 200.00.",
  limite_diario: "Superaste el máximo de S/ 500.00 en recargas de hoy.",
  idempotencia_conflicto: "Esta compra ya se inició con otro monto.",
  tarjeta_no_activa: "Esta tarjeta ya está bloqueada.",
  red: "No hay conexión. Revisa tu internet e intenta de nuevo.",
  servidor: "El servicio no está disponible. Intenta de nuevo en unos minutos.",
};

// Códigos que puede devolver cada función según api-cliente-v1.yaml.
const S = ["no_autenticado"];            // rutas con sesión
const M = ["prohibido"];                 // métodos que modifican (CSRF)
const ERRORES = {
  activarTarjeta: [...M, "validacion", "activacion_invalida", "bloqueo_temporal"],
  activacionCorreo: [...M, "validacion", "activacion_invalida"],
  activacionConfirmar: [...M, "validacion", "otp_invalido", "otp_expirado", "activacion_invalida"],
  ingresar: [...M, "validacion", "credenciales_invalidas", "bloqueo_temporal"],
  confirmarOtp: [...S, ...M, "validacion", "otp_invalido", "otp_expirado"],
  salir: [...S, ...M],
  recuperarContrasena: [...M, "validacion", "bloqueo_temporal"],
  restablecerContrasena: [...M, "validacion"],
  cambiarContrasena: [...S, ...M, "validacion", "credenciales_invalidas"],
  resumen: [...S],
  movimientos: [...S, "validacion"],
  perfil: [...S],
  cambiarCorreo: [...S, ...M, "validacion"],
  confirmarCorreo: [...S, ...M, "validacion", "otp_invalido", "otp_expirado"],
  accesos: [...S],
  dispositivos: [...S],
  olvidarDispositivo: [...S, ...M, "no_encontrado"],
  bloquearTarjeta: [...S, ...M, "validacion", "credenciales_invalidas", "no_encontrado", "tarjeta_no_activa"],
  reposiciones: [...S],
  solicitarReposicion: [...S, ...M, "validacion", "no_encontrado"],
  ordenes: [...S, "prohibido"],
  orden: [...S, "prohibido", "no_encontrado"],
  crearOrden: [...S, ...M, "validacion", "monto_fuera_de_limite", "limite_diario", "idempotencia_conflicto"],
};

function fallo(codigo, campos = {}, reintentarEn = null) {
  return new ErrorApi(codigo, MENSAJES[codigo], campos, ESTADO_HTTP[codigo], reintentarEn);
}

function parametro(nombre) {
  return new URLSearchParams(globalThis.location?.search ?? "").get(nombre);
}

// ------------------------------------------------------------- Fechas

const HORA = 3600e3;
const DIA = 24 * HORA;

// ISO 8601 con la zona de Perú (-05:00, sin horario de verano).
function iso(ms) {
  return new Date(ms - 5 * HORA).toISOString().slice(0, 19) + "-05:00";
}

function diaLima(ms) {
  return iso(ms).slice(0, 10);
}

// ------------------------------------------------------------- Datos iniciales

function datosIniciales() {
  const ahora = Date.now();
  // Las 00:00 de hoy en Lima, para poner los movimientos a horas razonables.
  const hoy = Date.parse(diaLima(ahora) + "T00:00:00-05:00");
  const rutas = ["Ruta 7", "Ruta 12", "Ruta 3", "Ruta 7", "Ruta 21"];
  const validadores = ["VAL-012", "VAL-031", "VAL-007", "VAL-012", "VAL-044"];

  // 44 movimientos, del más reciente al más antiguo: 2 viajes al día
  // (7:40 y 18:20), una recarga cada 5 días y un ajuste. Luego la emisión.
  const lista = [];
  let saldo = 1240;
  for (let i = 0; lista.length < 44; i++) {
    const dia = Math.floor(i / 2) + (Date.now() - hoy < 19 * HORA ? 1 : 0);
    const hora = i % 2 === 0 ? 18 * HORA + 20 * 60e3 : 7 * HORA + 40 * 60e3;
    const fecha = hoy - dia * DIA + hora + (i * 7 % 13) * 60e3;
    let mov;
    if (i === 17) {
      mov = { tipo: "ajuste", monto: 100, descripcion: "Devolución de pasaje cobrado dos veces", validador: null };
    } else if (i % 10 === 5) {
      mov = { tipo: "recarga", monto: 1000, descripcion: "Recarga en línea", validador: null };
    } else {
      const r = i % rutas.length;
      mov = { tipo: "viaje", monto: -100, descripcion: `Pasaje — ${rutas[r]}`, validador: validadores[r] };
    }
    lista.push({ fecha: iso(fecha), _ms: fecha, ...mov, saldo_final: saldo });
    saldo -= mov.monto;
  }
  const emitida = lista.at(-1)._ms - 2 * DIA;
  lista.push({
    fecha: iso(emitida), _ms: emitida, tipo: "emision", monto: saldo, saldo_final: saldo,
    descripcion: "Emisión de la tarjeta con saldo inicial", validador: null,
  });

  return {
    sesion: "activa",                // null | "a_medias" | "activa"
    contrasena: CONTRASENA,
    cliente: { nombre: "Ana Quispe", documento_enmascarado: "****8912", correo: "ana.quispe@example.com" },
    creado: iso(emitida + 11 * 60e3),
    correoPendiente: null,
    cuenta: { id: 1, saldo: 1240, por_cargar_en_tarjeta: 0, tarifa: { codigo: 1, nombre: "General", precio: 100 } },
    tarjetas: [{
      id: 7, numero_impreso: "123456782", estado: "activa",
      emitida: iso(emitida), ultimo_uso: lista.find((m) => m.tipo === "viaje").fecha,
    }],
    movimientos: lista,
    accesos: [
      { fecha: iso(ahora - 2 * HORA), ip: "190.117.32.8", dispositivo: "Chrome en Android", exito: true },
      { fecha: iso(ahora - 3 * DIA), ip: "190.117.32.8", dispositivo: "Chrome en Android", exito: true },
      { fecha: iso(ahora - 3 * DIA - 60e3), ip: "190.117.32.8", dispositivo: "Chrome en Android", exito: false },
      { fecha: iso(ahora - 9 * DIA), ip: "181.65.200.14", dispositivo: "Firefox en Windows", exito: true },
    ],
    dispositivos: [
      { id: 3, nombre: "Chrome en Android", ultimo_uso: iso(ahora - 2 * HORA), confiable_hasta: iso(ahora + 88 * DIA) },
      { id: 2, nombre: "Firefox en Windows", ultimo_uso: iso(ahora - 9 * DIA), confiable_hasta: iso(ahora + 81 * DIA) },
    ],
    dispositivoActual: 3,
    siguienteId: 100,
    reposiciones: [],
    ordenes: [],                     // con _ms, _clave
    activaciones: {},                // token → { paso, correo }
  };
}

// ------------------------------------------------------------- Estado

let memoria = null;

function estado() {
  if (memoria) return memoria;
  try {
    const guardado = globalThis.sessionStorage?.getItem(CLAVE_ESTADO);
    if (guardado) memoria = JSON.parse(guardado);
  } catch { /* sin sessionStorage o dato corrupto: se regenera */ }
  memoria ??= datosIniciales();
  return memoria;
}

function guardar() {
  try {
    globalThis.sessionStorage?.setItem(CLAVE_ESTADO, JSON.stringify(memoria));
  } catch { /* sin sessionStorage: solo en memoria */ }
}

const copia = (v) => (v === undefined || v === null ? v ?? null : JSON.parse(JSON.stringify(v)));
const sinInternos = (o) => Object.fromEntries(Object.entries(o).filter(([k]) => !k.startsWith("_")));

// Ejecuta una función simulada: espera, aplica ?error= y guarda el estado.
function simular(nombre, cuerpo) {
  return new Promise((resolver, rechazar) => {
    setTimeout(() => {
      try {
        const forzado = parametro("error");
        if (forzado && (forzado === "red" || forzado === "servidor" || ERRORES[nombre].includes(forzado))) {
          const espera = forzado === "bloqueo_temporal" ? Number(parametro("reintentar_en") ?? 90) : null;
          throw fallo(forzado, forzado === "validacion" ? { general: ["Error simulado."] } : {}, espera);
        }
        const e = estado();
        const r = cuerpo(e);
        guardar();
        resolver(copia(r));
      } catch (err) {
        rechazar(err);
      }
    }, RETARDO);
  });
}

function conSesion(e) {
  if (e.sesion !== "activa") throw fallo("no_autenticado");
}

const esDocumento = (v) => /^[0-9]{8}$/.test(String(v ?? ""));
const esCorreo = (v) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(v ?? ""));

function luhn(numero) {
  let suma = 0;
  [...numero].reverse().forEach((c, i) => {
    let d = Number(c);
    if (i % 2 === 1) { d *= 2; if (d > 9) d -= 9; }
    suma += d;
  });
  return suma % 10 === 0;
}

function revisarOtp(otp) {
  if (!/^[0-9]{6}$/.test(String(otp ?? ""))) throw fallo("validacion", { otp: ["Son 6 dígitos."] });
  if (otp !== OTP) throw fallo("otp_invalido", { otp: ["Código incorrecto."] });
}

function revisarContrasenaNueva(contrasena, campo) {
  if (typeof contrasena !== "string" || contrasena.length < 8 || /^[0-9]+$/.test(contrasena)) {
    throw fallo("validacion", { [campo]: ["Mínimo 8 caracteres y no solo números."] });
  }
}

function registrarAcceso(e, exito) {
  e.accesos.unshift({ fecha: iso(Date.now()), ip: "190.117.32.8", dispositivo: "Este navegador", exito });
  e.accesos.length = Math.min(e.accesos.length, 20);
}

// Avanza las órdenes según el tiempo transcurrido (se paga a los 2 s y se
// acredita a los 4 s) y acredita en la cuenta las que lleguen a "acreditada".
function avanzarOrdenes(e) {
  const ahora = Date.now();
  for (const o of e.ordenes) {
    if (!["creada", "pendiente", "pagada"].includes(o.estado)) continue;
    const pasado = ahora - o._ms;
    if (o.monto === 888) continue;
    if (o.monto === 666 && pasado >= 2000) { o.estado = "fallida"; continue; }
    if (o.monto === 777 && pasado >= 2000) { o.estado = "expirada"; continue; }
    if (o.monto === 666 || o.monto === 777) continue;
    if (pasado >= 2000 && !o.pagada_en) {
      o.estado = "pagada";
      o.pagada_en = iso(o._ms + 2000);
    }
    if (pasado >= 4000) {
      o.estado = "acreditada";
      o.acreditada_en = iso(o._ms + 4000);
      e.cuenta.saldo += o.monto;
      e.cuenta.por_cargar_en_tarjeta += o.monto;
      e.movimientos.unshift({
        fecha: o.acreditada_en, _ms: o._ms + 4000, tipo: "recarga", monto: o.monto,
        saldo_final: e.cuenta.saldo, descripcion: "Recarga en línea", validador: null,
      });
    }
  }
}

const detalleOrden = (o) => ({
  id: o.id, estado: o.estado, monto: o.monto, creada: o.creada,
  pagada_en: o.pagada_en, acreditada_en: o.acreditada_en,
});

function uuidSimulado(n) {
  const h = n.toString(16).padStart(12, "0");
  return `5e000000-0000-4000-8000-${h}`;
}

// ------------------------------------------------------------- Activación

export function activarTarjeta(documento, numeroTarjeta, codigo) {
  return simular("activarTarjeta", (e) => {
    const campos = {};
    if (!esDocumento(documento)) campos.documento = ["El DNI tiene 8 dígitos."];
    if (!/^[0-9]{9}$/.test(String(numeroTarjeta ?? ""))) campos.numero_tarjeta = ["Son los 9 dígitos impresos."];
    if (!/^[A-Z0-9]{8}$/.test(String(codigo ?? ""))) campos.codigo = ["Son 8 letras o números."];
    if (Object.keys(campos).length) throw fallo("validacion", campos);
    if (!luhn(numeroTarjeta)) throw fallo("activacion_invalida");
    const token = `act_sim_${e.siguienteId++}`;
    e.activaciones[token] = { paso: 1, correo: null };
    return { token_activacion: token, nombre_enmascarado: "A** Q*****" };
  });
}

export function activacionCorreo(tokenActivacion, correo) {
  return simular("activacionCorreo", (e) => {
    const act = e.activaciones[tokenActivacion];
    if (!act) throw fallo("activacion_invalida");
    if (!esCorreo(correo)) throw fallo("validacion", { correo: ["Correo no válido."] });
    act.paso = 2;
    act.correo = correo;
    return { enviado: true };
  });
}

export function activacionConfirmar(tokenActivacion, otp, contrasena) {
  return simular("activacionConfirmar", (e) => {
    const act = e.activaciones[tokenActivacion];
    if (!act || act.paso !== 2) throw fallo("activacion_invalida");
    revisarOtp(otp);
    revisarContrasenaNueva(contrasena, "contrasena");
    delete e.activaciones[tokenActivacion];
    e.cliente.correo = act.correo;
    e.contrasena = contrasena;
    e.sesion = "activa";
    return { ok: true };
  });
}

// ------------------------------------------------------------- Sesión

export function ingresar(documento, contrasena) {
  return simular("ingresar", (e) => {
    if (!esDocumento(documento)) throw fallo("validacion", { documento: ["El DNI tiene 8 dígitos."] });
    if (documento !== DOCUMENTO || contrasena !== e.contrasena) {
      registrarAcceso(e, false);
      throw fallo("credenciales_invalidas");
    }
    const confiable = e.dispositivos.some((d) => d.id === e.dispositivoActual);
    e.sesion = confiable ? "activa" : "a_medias";
    if (confiable) registrarAcceso(e, true);
    return { requiere_otp: !confiable };
  });
}

export function confirmarOtp(otp, recordarDispositivo = false) {
  return simular("confirmarOtp", (e) => {
    if (e.sesion !== "a_medias") throw fallo("no_autenticado");
    revisarOtp(otp);
    e.sesion = "activa";
    registrarAcceso(e, true);
    if (recordarDispositivo) {
      e.dispositivoActual = e.siguienteId++;
      e.dispositivos.unshift({
        id: e.dispositivoActual, nombre: "Este navegador",
        ultimo_uso: iso(Date.now()), confiable_hasta: iso(Date.now() + 90 * DIA),
      });
    }
    return { ok: true };
  });
}

export function salir() {
  return simular("salir", (e) => {
    conSesion(e);
    e.sesion = null;
    return null;
  });
}

// ------------------------------------------------------------- Contraseña

export function recuperarContrasena(documento) {
  return simular("recuperarContrasena", () => {
    if (!esDocumento(documento)) throw fallo("validacion", { documento: ["El DNI tiene 8 dígitos."] });
    return null;
  });
}

export function restablecerContrasena(uid, token, contrasena) {
  return simular("restablecerContrasena", (e) => {
    if (!uid || !token) throw fallo("validacion", { token: ["Enlace inválido o vencido."] });
    revisarContrasenaNueva(contrasena, "contrasena");
    e.contrasena = contrasena;
    e.sesion = null;
    return { ok: true };
  });
}

export function cambiarContrasena(actual, nueva) {
  return simular("cambiarContrasena", (e) => {
    conSesion(e);
    if (actual !== e.contrasena) throw fallo("credenciales_invalidas", { actual: ["Contraseña incorrecta."] });
    revisarContrasenaNueva(nueva, "nueva");
    e.contrasena = nueva;
    return { ok: true };
  });
}

// ------------------------------------------------------------- Consulta

export function resumen() {
  return simular("resumen", (e) => {
    conSesion(e);
    avanzarOrdenes(e);
    return { cliente: e.cliente, cuenta: e.cuenta, tarjetas: e.tarjetas };
  });
}

export function movimientos(filtros = {}) {
  return simular("movimientos", (e) => {
    conSesion(e);
    avanzarOrdenes(e);
    const { desde, hasta, tipo } = filtros;
    const pagina = Number(filtros.pagina ?? 1);
    const campos = {};
    if (tipo && !["viaje", "recarga", "emision", "ajuste"].includes(tipo)) campos.tipo = ["Tipo no válido."];
    if (!Number.isInteger(pagina) || pagina < 1) campos.pagina = ["Página no válida."];
    for (const [k, v] of [["desde", desde], ["hasta", hasta]]) {
      if (v && !/^\d{4}-\d{2}-\d{2}$/.test(v)) campos[k] = ["Usa el formato AAAA-MM-DD."];
    }
    if (Object.keys(campos).length) throw fallo("validacion", campos);
    const lista = e.movimientos.filter((m) => {
      const dia = m.fecha.slice(0, 10);
      return (!tipo || m.tipo === tipo) && (!desde || dia >= desde) && (!hasta || dia <= hasta);
    });
    const paginas = Math.ceil(lista.length / 20);
    return {
      resultados: lista.slice((pagina - 1) * 20, pagina * 20).map(sinInternos),
      pagina,
      paginas,
    };
  });
}

export function perfil() {
  return simular("perfil", (e) => {
    conSesion(e);
    return { ...e.cliente, creado: e.creado };
  });
}

export function cambiarCorreo(correo) {
  return simular("cambiarCorreo", (e) => {
    conSesion(e);
    if (!esCorreo(correo)) throw fallo("validacion", { correo: ["Correo no válido."] });
    e.correoPendiente = correo;
    return { enviado: true };
  });
}

export function confirmarCorreo(correo, otp) {
  return simular("confirmarCorreo", (e) => {
    conSesion(e);
    if (!e.correoPendiente || correo !== e.correoPendiente) {
      throw fallo("validacion", { correo: ["Primero pide el código para este correo."] });
    }
    revisarOtp(otp);
    e.cliente.correo = correo;
    e.correoPendiente = null;
    return { ok: true };
  });
}

export function accesos() {
  return simular("accesos", (e) => {
    conSesion(e);
    return e.accesos;
  });
}

export function dispositivos() {
  return simular("dispositivos", (e) => {
    conSesion(e);
    return e.dispositivos;
  });
}

export function olvidarDispositivo(id) {
  return simular("olvidarDispositivo", (e) => {
    conSesion(e);
    const i = e.dispositivos.findIndex((d) => d.id === Number(id));
    if (i < 0) throw fallo("no_encontrado");
    e.dispositivos.splice(i, 1);
    return null;
  });
}

// ------------------------------------------------------------- Tarjeta

export function bloquearTarjeta(id, motivo, contrasena) {
  return simular("bloquearTarjeta", (e) => {
    conSesion(e);
    const t = e.tarjetas.find((x) => x.id === Number(id));
    if (!t) throw fallo("no_encontrado");
    if (!["robo", "perdida"].includes(motivo)) throw fallo("validacion", { motivo: ["Elige robo o pérdida."] });
    if (contrasena !== e.contrasena) throw fallo("credenciales_invalidas", { contrasena: ["Contraseña incorrecta."] });
    if (t.estado !== "activa") throw fallo("tarjeta_no_activa");
    t.estado = "bloqueada";
    t.bloqueada_en = iso(Date.now());
    return { estado: "bloqueada", bloqueada_en: t.bloqueada_en };
  });
}

export function reposiciones() {
  return simular("reposiciones", (e) => {
    conSesion(e);
    return e.reposiciones;
  });
}

export function solicitarReposicion(tarjetaId) {
  return simular("solicitarReposicion", (e) => {
    conSesion(e);
    const t = e.tarjetas.find((x) => x.id === Number(tarjetaId));
    if (!t) throw fallo("no_encontrado");
    if (t.estado !== "bloqueada") {
      throw fallo("validacion", { tarjeta_id: ["Solo se repone una tarjeta bloqueada."] });
    }
    if (e.reposiciones.some((r) => r.tarjeta_id === t.id)) {
      throw fallo("validacion", { tarjeta_id: ["Ya pediste la reposición de esta tarjeta."] });
    }
    const r = { id: e.siguienteId++, tarjeta_id: t.id, estado: "solicitada", creada: iso(Date.now()) };
    e.reposiciones.unshift(r);
    return r;
  });
}

// ------------------------------------------------------------- Recargas

export function ordenes() {
  return simular("ordenes", (e) => {
    conSesion(e);
    avanzarOrdenes(e);
    return e.ordenes.map(detalleOrden);
  });
}

export function orden(id) {
  return simular("orden", (e) => {
    conSesion(e);
    avanzarOrdenes(e);
    const o = e.ordenes.find((x) => x.id === id);
    if (!o) throw fallo("no_encontrado");
    return detalleOrden(o);
  });
}

export function crearOrden(monto, clave = nuevaClave()) {
  return simular("crearOrden", (e) => {
    conSesion(e);
    avanzarOrdenes(e);
    if (!clave) throw fallo("validacion", { "Idempotency-Key": ["Obligatoria."] });
    if (!Number.isInteger(monto)) throw fallo("validacion", { monto: ["El monto va en céntimos (entero)."] });
    const previa = e.ordenes.find((o) => o._clave === clave);
    if (previa) {
      if (previa.monto !== monto) throw fallo("idempotencia_conflicto");
      return salidaCreada(previa);
    }
    if (monto < 100 || monto > 20000) throw fallo("monto_fuera_de_limite", { monto: ["Fuera de límite."] });
    const hoy = diaLima(Date.now());
    const pagadoHoy = e.ordenes
      .filter((o) => ["pagada", "acreditada"].includes(o.estado) && o.creada.slice(0, 10) === hoy)
      .reduce((s, o) => s + o.monto, 0);
    const pendientes = e.ordenes.filter((o) => ["creada", "pendiente"].includes(o.estado)).length;
    if (pagadoHoy + monto > 50000 || pendientes >= 3) throw fallo("limite_diario");
    const ahora = Date.now();
    const id = uuidSimulado(e.siguienteId++);
    const o = {
      id, estado: "pendiente", monto, creada: iso(ahora), pagada_en: null, acreditada_en: null,
      _ms: ahora, _clave: clave, expira: iso(ahora + 30 * 60e3),
      // Sin pasarela: el "pago" lleva directo a la página de resultado.
      url_pago: `/recargar/resultado?orden=${id}&simulado=1`,
    };
    e.ordenes.unshift(o);
    return salidaCreada(o);
  });
}

const salidaCreada = (o) => ({ id: o.id, estado: o.estado, monto: o.monto, url_pago: o.url_pago, expira: o.expira });

