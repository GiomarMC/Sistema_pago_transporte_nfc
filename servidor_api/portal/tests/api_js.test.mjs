// Pruebas de api.js y api-simulada.js (M0-04).
// Ejecutar desde la raíz del repositorio:  node --test servidor_api/portal/tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const DIR_JS = new URL("../static/portal/js/", import.meta.url);
const YAML = readFileSync(new URL("../../../docs/contratos/api-cliente-v1.yaml", import.meta.url), "utf8");

globalThis.location = { search: "" };
const real = await import(new URL("api.js", DIR_JS));
const cargarSimulada = (n = Math.random()) => import(new URL(`api-simulada.js?i=${n}`, DIR_JS));
const sim = await cargarSimulada(0);

// --------------------------------------------------------------- Contrato (YAML)

// Lee del YAML: operationId → { metodo, ruta, estados[] }.
function operacionesDelYaml() {
  const ops = {};
  let ruta = null, metodo = null, enPaths = false, actual = null;
  for (const linea of YAML.split("\n")) {
    if (/^paths:/.test(linea)) { enPaths = true; continue; }
    if (/^components:/.test(linea)) break;
    if (!enPaths) continue;
    let m;
    if ((m = linea.match(/^ {2}(\/\S*):\s*$/))) { ruta = m[1]; continue; }
    if ((m = linea.match(/^ {4}(get|post|delete|put|patch):\s*$/))) { metodo = m[1].toUpperCase(); continue; }
    if ((m = linea.match(/^ {6}operationId:\s*(\w+)/))) {
      actual = ops[m[1]] = { metodo, ruta, estados: [] };
      continue;
    }
    if (actual && (m = linea.match(/^ {8}"(\d{3})":/))) actual.estados.push(Number(m[1]));
  }
  return ops;
}
const OPS = operacionesDelYaml();
const CODIGOS_C4 = YAML.match(/CodigoError:\n\s+type: string\n\s+enum:\n((?:\s+- \w+\n)+)/)[1]
  .match(/\w+/g);
const ESTADO_HTTP = {
  no_autenticado: 401, prohibido: 403, no_encontrado: 404, validacion: 400,
  credenciales_invalidas: 401, bloqueo_temporal: 429, otp_invalido: 400,
  otp_expirado: 400, activacion_invalida: 400, monto_fuera_de_limite: 400,
  limite_diario: 400, idempotencia_conflicto: 409, tarjeta_no_activa: 409,
};

// Argumentos de ejemplo y lo que api.js debe enviar con ellos.
const CASOS = {
  activarTarjeta: [["45678912", "123456782", "K7Q2M9XA"], { documento: "45678912", numero_tarjeta: "123456782", codigo: "K7Q2M9XA" }],
  activacionCorreo: [["tok", "a@b.pe"], { token_activacion: "tok", correo: "a@b.pe" }],
  activacionConfirmar: [["tok", "123456", "clave-larga"], { token_activacion: "tok", otp: "123456", contrasena: "clave-larga" }],
  ingresar: [["45678912", "subepe123"], { documento: "45678912", contrasena: "subepe123" }],
  confirmarOtp: [["123456", true], { otp: "123456", recordar_dispositivo: true }],
  salir: [[], undefined],
  recuperarContrasena: [["45678912"], { documento: "45678912" }],
  restablecerContrasena: [["MTI", "tk", "clave-larga"], { uid: "MTI", token: "tk", contrasena: "clave-larga" }],
  cambiarContrasena: [["subepe123", "clave-larga"], { actual: "subepe123", nueva: "clave-larga" }],
  resumen: [[], undefined],
  movimientos: [[{ tipo: "viaje", pagina: 2, desde: "", hasta: null }], undefined, "?tipo=viaje&pagina=2"],
  perfil: [[], undefined],
  cambiarCorreo: [["n@b.pe"], { correo: "n@b.pe" }],
  confirmarCorreo: [["n@b.pe", "123456"], { correo: "n@b.pe", otp: "123456" }],
  accesos: [[], undefined],
  dispositivos: [[], undefined],
  olvidarDispositivo: [[3], undefined, "", { id: "3" }],
  bloquearTarjeta: [[7, "robo", "subepe123"], { motivo: "robo", contrasena: "subepe123" }, "", { id: "7" }],
  reposiciones: [[], undefined],
  solicitarReposicion: [[7], { tarjeta_id: 7 }],
  ordenes: [[], undefined],
  orden: [["6f1c0a8e-2b3d-4c5e-9f70-1a2b3c4d5e6f"], undefined, "", { id: "6f1c0a8e-2b3d-4c5e-9f70-1a2b3c4d5e6f" }],
  crearOrden: [[1000, "0b7d6c9e-3a4f-4e21-8d5b-9c1e2f3a4b5c"], { monto: 1000 }],
};

// --------------------------------------------------------------- Firmas

function parametros(fn) {
  const s = fn.toString();
  let i = s.indexOf("("), prof = 0;
  for (let j = i; j < s.length; j++) {
    if (s[j] === "(") prof++;
    if (s[j] === ")" && --prof === 0) return s.slice(i + 1, j).replace(/\s+/g, " ").trim();
  }
  throw new Error("sin parámetros");
}

test("api.js y api-simulada.js exportan lo mismo con las mismas firmas", () => {
  assert.deepEqual(Object.keys(sim).sort(), Object.keys(real).sort());
  for (const nombre of Object.keys(real)) {
    if (typeof real[nombre] !== "function" || nombre === "ErrorApi") continue;
    assert.equal(parametros(sim[nombre]), parametros(real[nombre]), `firma de ${nombre}`);
    assert.equal(sim[nombre].length, real[nombre].length, `aridad de ${nombre}`);
  }
  assert.equal(sim.ErrorApi, real.ErrorApi);
});

test("hay una función por cada operación del OpenAPI", () => {
  const auxiliares = ["ErrorApi", "esSimulado", "cargarApi", "nuevaClave", "BASE"];
  const funciones = Object.keys(real).filter((k) => !auxiliares.includes(k)).sort();
  assert.deepEqual(funciones, Object.keys(OPS).sort());
  assert.deepEqual(Object.keys(CASOS).sort(), funciones);
});

// --------------------------------------------------------------- api.js

function simularFetch(respuesta) {
  const pedidos = [];
  globalThis.fetch = async (url, opciones) => {
    pedidos.push({ url, ...opciones });
    return typeof respuesta === "function" ? respuesta() : respuesta;
  };
  return pedidos;
}

const json = (estado, cuerpo) => new Response(cuerpo === undefined ? null : JSON.stringify(cuerpo), {
  status: estado, headers: { "Content-Type": "application/json" },
});

test("api.js llama a cada ruta con su método, cuerpo, cookie y CSRF", async () => {
  globalThis.document = { cookie: "otra=1; csrftoken=abc%3D123; x=2" };
  for (const [nombre, [args, cuerpo, consulta = "", vars = {}]] of Object.entries(CASOS)) {
    const pedidos = simularFetch(json(200, { ok: true }));
    await real[nombre](...args);
    const op = OPS[nombre];
    const ruta = op.ruta.replace(/{(\w+)}/g, (_, v) => vars[v]);
    const p = pedidos[0];
    assert.equal(p.url, `/api/v1${ruta}${consulta}`, nombre);
    assert.equal(p.method, op.metodo, nombre);
    assert.equal(p.credentials, "same-origin", nombre);
    assert.equal(p.headers["X-CSRFToken"], op.metodo === "GET" ? undefined : "abc=123", nombre);
    assert.deepEqual(p.body === undefined ? undefined : JSON.parse(p.body), cuerpo, nombre);
  }
});

test("crearOrden manda Idempotency-Key y genera una si no se pasa", async () => {
  let pedidos = simularFetch(json(201, {}));
  await real.crearOrden(500, "clave-fija");
  assert.equal(pedidos[0].headers["Idempotency-Key"], "clave-fija");
  pedidos = simularFetch(json(201, {}));
  await real.crearOrden(500);
  assert.match(pedidos[0].headers["Idempotency-Key"], /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test("api.js convierte las respuestas de error en ErrorApi", async () => {
  simularFetch(() => json(400, { error: { codigo: "validacion", mensaje: "Revisa", campos: { monto: ["x"] } } }));
  await assert.rejects(real.crearOrden(1), (e) =>
    e instanceof real.ErrorApi && e.codigo === "validacion" && e.mensaje === "Revisa" &&
    e.campos.monto[0] === "x" && e.estado === 400);

  simularFetch(() => json(429, { error: { codigo: "bloqueo_temporal", mensaje: "Espera", campos: {}, reintentar_en: 60 } }));
  await assert.rejects(real.ingresar("1", "2"), (e) => e.codigo === "bloqueo_temporal" && e.reintentarEn === 60);

  simularFetch(() => new Response("<html>502</html>", { status: 502 }));
  await assert.rejects(real.resumen(), (e) => e.codigo === "servidor" && e.estado === 502);

  simularFetch(() => new Response("", { status: 401 }));
  await assert.rejects(real.resumen(), (e) => e.codigo === "no_autenticado");

  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(real.resumen(), (e) => e.codigo === "red");

  simularFetch(() => new Response(null, { status: 204 }));
  assert.equal(await real.salir(), null);
  simularFetch(() => new Response(null, { status: 202 }));
  assert.equal(await real.recuperarContrasena("45678912"), null);
});

test("esSimulado() lee ?simulado= y lo recuerda en localStorage", () => {
  const datos = {};
  globalThis.localStorage = {
    getItem: (k) => datos[k] ?? null, setItem: (k, v) => { datos[k] = v; }, removeItem: (k) => { delete datos[k]; },
  };
  try {
    globalThis.location = { search: "" };
    assert.equal(real.esSimulado(), false);
    globalThis.location = { search: "?simulado=1" };
    assert.equal(real.esSimulado(), true);
    globalThis.location = { search: "" };
    assert.equal(real.esSimulado(), true, "sigue simulado en la página siguiente");
    globalThis.location = { search: "?simulado=0" };
    assert.equal(real.esSimulado(), false);
  } finally {
    delete globalThis.localStorage;
    globalThis.location = { search: "" };
  }
});

// --------------------------------------------------------------- Simulada

test("los datos simulados son coherentes", async () => {
  const api = await cargarSimulada();
  const r = await api.resumen();
  assert.equal(r.cliente.nombre, "Ana Quispe");
  assert.equal(r.cuenta.saldo, 1240);
  assert.equal(r.tarjetas.length, 1);
  assert.equal(r.tarjetas[0].estado, "activa");

  const paginas = [];
  for (let p = 1; p <= 3; p++) paginas.push(await api.movimientos({ pagina: p }));
  assert.deepEqual(paginas.map((x) => x.resultados.length), [20, 20, 5]);
  assert.ok(paginas.every((x) => x.paginas === 3));
  const todos = paginas.flatMap((x) => x.resultados);
  assert.equal(todos[0].saldo_final, 1240);
  for (let i = 0; i < todos.length - 1; i++) {
    assert.equal(todos[i].saldo_final - todos[i].monto, todos[i + 1].saldo_final, `saldo encadenado en ${i}`);
    assert.ok(todos[i].fecha >= todos[i + 1].fecha, "orden por fecha");
    assert.ok(todos[i].saldo_final >= 0);
  }
  assert.equal(todos.at(-1).tipo, "emision");
  assert.equal(todos.at(-1).monto, todos.at(-1).saldo_final);
  assert.ok(todos[0].fecha <= new Date(Date.now() - 5 * 3600e3).toISOString().slice(0, 19) + "-05:00", "nada en el futuro");
  for (const m of todos) assert.match(m.fecha, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}-05:00$/);

  const viajes = await api.movimientos({ tipo: "viaje" });
  assert.ok(viajes.resultados.every((m) => m.tipo === "viaje" && m.validador));
  const dia = todos[3].fecha.slice(0, 10);
  const deUnDia = await api.movimientos({ desde: dia, hasta: dia });
  assert.ok(deUnDia.resultados.length >= 1 && deUnDia.resultados.every((m) => m.fecha.startsWith(dia)));
  const vacio = await api.movimientos({ desde: "2000-01-01", hasta: "2000-01-02" });
  assert.deepEqual([vacio.resultados.length, vacio.paginas], [0, 0]);
});

test("las respuestas simuladas tardan unos 300 ms", async () => {
  const api = await cargarSimulada();
  const t0 = performance.now();
  await api.perfil();
  const t = performance.now() - t0;
  assert.ok(t >= 290 && t < 600, `tardó ${t} ms`);
});

test("?error=<codigo> genera cada código de C4 con su estado HTTP", async () => {
  const generados = new Set();
  try {
    for (const codigo of [...CODIGOS_C4, "red", "servidor"]) {
      globalThis.location = { search: `?error=${codigo}&reintentar_en=45` };
      const api = await cargarSimulada();
      const res = await Promise.all(Object.entries(CASOS).map(([nombre, [args]]) =>
        api[nombre](...args).then(() => [nombre, null], (e) => [nombre, e])));
      for (const [nombre, e] of res) {
        if (!e || e.codigo !== codigo) continue;
        assert.ok(e instanceof real.ErrorApi);
        assert.ok(e.mensaje, `${codigo} sin mensaje`);
        if (codigo in ESTADO_HTTP) {
          assert.equal(e.estado, ESTADO_HTTP[codigo]);
          assert.ok(OPS[nombre].estados.includes(e.estado),
            `${nombre} simula ${codigo} (${e.estado}) pero el OpenAPI no lo declara`);
        }
        if (codigo === "bloqueo_temporal") assert.equal(e.reintentarEn, 45);
        generados.add(codigo);
      }
    }
  } finally {
    globalThis.location = { search: "" };
  }
  assert.deepEqual([...generados].sort(), [...CODIGOS_C4, "red", "servidor"].sort());
});

test("simulada: sesión, bloqueo de tarjeta y reposición", async () => {
  const api = await cargarSimulada();
  await api.salir();
  await assert.rejects(api.resumen(), (e) => e.codigo === "no_autenticado");
  await assert.rejects(api.ingresar("45678912", "mala"), (e) => e.codigo === "credenciales_invalidas");
  assert.deepEqual(await api.ingresar("45678912", "subepe123"), { requiere_otp: false });

  await assert.rejects(api.solicitarReposicion(7), (e) => e.codigo === "validacion");
  await assert.rejects(api.bloquearTarjeta(7, "perdida", "mala"), (e) => e.codigo === "credenciales_invalidas");
  await assert.rejects(api.bloquearTarjeta(99, "perdida", "subepe123"), (e) => e.codigo === "no_encontrado");
  const b = await api.bloquearTarjeta(7, "perdida", "subepe123");
  assert.equal(b.estado, "bloqueada");
  await assert.rejects(api.bloquearTarjeta(7, "robo", "subepe123"), (e) => e.codigo === "tarjeta_no_activa");
  const rep = await api.solicitarReposicion(7);
  assert.equal(rep.estado, "solicitada");
  assert.deepEqual((await api.reposiciones()).map((x) => x.id), [rep.id]);
  assert.equal((await api.resumen()).tarjetas[0].estado, "bloqueada");
});

test("simulada: crearOrden es idempotente y la orden se acredita", async () => {
  const api = await cargarSimulada();
  await assert.rejects(api.crearOrden(50, "k0"), (e) => e.codigo === "monto_fuera_de_limite");
  const [a, b] = await Promise.all([api.crearOrden(1000, "k1"), api.crearOrden(1000, "k1")]);
  assert.equal(a.id, b.id, "doble clic → una sola orden");
  await assert.rejects(api.crearOrden(2000, "k1"), (e) => e.codigo === "idempotencia_conflicto");
  const falla = await api.crearOrden(666, "k2");
  const queda = await api.crearOrden(888, "k3");
  await assert.rejects(api.crearOrden(500, "k4"), (e) => e.codigo === "limite_diario", "máximo 3 pendientes");
  assert.equal((await api.ordenes()).length, 3);

  await new Promise((r) => setTimeout(r, 4000));
  const [oa, of, oq, res] = await Promise.all([api.orden(a.id), api.orden(falla.id), api.orden(queda.id), api.resumen()]);
  assert.equal(oa.estado, "acreditada");
  assert.ok(oa.pagada_en && oa.acreditada_en);
  assert.equal(of.estado, "fallida");
  assert.equal(oq.estado, "pendiente");
  assert.equal(res.cuenta.saldo, 1240 + 1000);
  assert.equal(res.cuenta.por_cargar_en_tarjeta, 1000);
  const ultimo = (await api.movimientos()).resultados[0];
  assert.deepEqual([ultimo.tipo, ultimo.monto, ultimo.saldo_final], ["recarga", 1000, 2240]);
  await assert.rejects(api.orden("no-existe"), (e) => e.codigo === "no_encontrado");
});
