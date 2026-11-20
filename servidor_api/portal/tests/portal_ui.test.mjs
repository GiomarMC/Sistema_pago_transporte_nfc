// Pruebas de la interfaz del portal (M14): el adaptador cliente.js sobre el cliente C4
// y la clave de compra de intentos.js.
// Ejecutar desde la raíz del repositorio:  node --test servidor_api/portal/tests/
import { test } from "node:test";
import assert from "node:assert/strict";

const DIR_JS = new URL("../static/portal/js/", import.meta.url);

const memoria = () => {
  const datos = new Map();
  return {
    getItem: (k) => datos.get(k) ?? null, setItem: (k, v) => datos.set(k, String(v)),
    removeItem: (k) => datos.delete(k), clear: () => datos.clear(),
  };
};
globalThis.localStorage = memoria();
globalThis.sessionStorage = memoria();

// Un cliente.js nuevo por prueba: elige modo simulado o real según location.search.
async function cargarCliente(search) {
  globalThis.location = { search };
  globalThis.sessionStorage.clear();
  globalThis.localStorage.clear();
  return import(new URL(`cliente.js?i=${Math.random()}`, DIR_JS));
}
// El simulador (api-simulada.js) guarda su estado en el módulo: las pruebas de este bloque
// comparten datos y van en orden (sesión → bloqueo temporal → movimientos → recarga → tarjeta
// → activación → perfil → recuperación).
const entrarConClave = async (api, clave) => {
  const { requiere_otp: pideOtp } = await api.ingresar({ dni: "45678912", clave });
  if (pideOtp) await api.confirmarOtp({ codigo: "123456", recordar: false });
};

// ------------------------------------------------------------- Modo simulado

test("sesión: ingresar, OTP y salir", async () => {
  const { api, simulado } = await cargarCliente("?simulado=1");
  assert.equal(simulado, true);
  assert.equal(await api.sesion(), true);
  await api.salir();
  assert.equal(await api.sesion(), false);
  await assert.rejects(api.ingresar({ dni: "45678912", clave: "mala" }), { codigo: "credenciales_invalidas" });
  // El dispositivo de prueba ya es de confianza: entra sin OTP
  assert.equal((await api.ingresar({ dni: "45678912", clave: "subepe123" })).requiere_otp, false);
  // Si se olvida, pide el código
  await api.quitarDispositivo({ id: 3 });
  await api.salir();
  assert.equal((await api.ingresar({ dni: "45678912", clave: "subepe123" })).requiere_otp, true);
  await assert.rejects(api.confirmarOtp({ codigo: "000000", recordar: false }), { codigo: "otp_invalido" });
  await api.confirmarOtp({ codigo: "123456", recordar: true });
  assert.equal(await api.sesion(), true);
});

test("el bloqueo temporal llega con reintentar_en, como lo lee la página", async () => {
  const { api } = await cargarCliente("?simulado=1&error=bloqueo_temporal&reintentar_en=45");
  await assert.rejects(api.ingresar({ dni: "45678912", clave: "x" }),
    (e) => e.codigo === "bloqueo_temporal" && e.reintentar_en === 45);
});

test("movimientos: 45 en páginas de 20 y filtro por tipo", async () => {
  const { api } = await cargarCliente("?simulado=1");
  assert.equal((await api.movimientos({ pagina: 1 })).resultados.length, 20);
  const ultima = await api.movimientos({ pagina: 3 });
  assert.equal(ultima.resultados.length, 5);
  assert.equal(ultima.paginas, 3);
  const recargas = await api.movimientos({ tipo: "recarga" });
  assert.ok(recargas.resultados.length > 0);
  assert.ok(recargas.resultados.every((m) => m.tipo === "recarga"));
});

test("recarga idempotente: la misma clave devuelve la misma orden", async () => {
  const { api } = await cargarCliente("?simulado=1");
  const o = await api.crearOrden({ monto: 1000, idempotencia: "clave-1" });
  assert.equal((await api.crearOrden({ monto: 1000, idempotencia: "clave-1" })).id, o.id);
  await assert.rejects(api.crearOrden({ monto: 2000, idempotencia: "clave-1" }), { codigo: "idempotencia_conflicto" });
  await assert.rejects(api.crearOrden({ monto: 50, idempotencia: "clave-2" }), { codigo: "monto_fuera_de_limite" });
  assert.equal((await api.orden(o.id)).monto, 1000);
});

test("bloqueo de tarjeta con contraseña y solicitud de reposición", async () => {
  const { api } = await cargarCliente("?simulado=1");
  const { tarjetas } = await api.resumen();
  await assert.rejects(api.bloquear({ id: tarjetas[0].id, motivo: "robo", clave: "mala" }), { codigo: "credenciales_invalidas" });
  await api.bloquear({ id: tarjetas[0].id, motivo: "robo", clave: "subepe123" });
  assert.equal((await api.resumen()).tarjetas[0].estado, "bloqueada");
  const s = await api.solicitarReposicion({ tarjeta_id: tarjetas[0].id });
  assert.equal(s.estado, "solicitada");
  assert.equal((await api.reposiciones())[0].id, s.id);
});

test("activación en tres pasos", async () => {
  const { api } = await cargarCliente("?simulado=1");
  await api.salir();
  await assert.rejects(api.activarIdentidad({ dni: "45678912", tarjeta: "123456789", codigo: "K7Q2M9XA" }), { codigo: "activacion_invalida" });
  const { token_activacion: token } = await api.activarIdentidad({ dni: "45678912", tarjeta: "123456782", codigo: "K7Q2M9XA" });
  await api.activarCorreo({ token, correo: "ana@example.test" });
  await assert.rejects(api.activarConfirmar({ token, codigo: "000000", clave: "NuevaClave2026" }), { codigo: "otp_invalido" });
  await api.activarConfirmar({ token, codigo: "123456", clave: "NuevaClave2026" });
  assert.equal(await api.sesion(), true);
});

test("perfil: el cambio de correo exige el código y manda el correo pendiente", async () => {
  const { api } = await cargarCliente("?simulado=1");
  await api.pedirCambioCorreo({ correo: "nuevo@example.test" });
  await assert.rejects(api.confirmarCorreo({ correo: "nuevo@example.test", codigo: "000000" }), { codigo: "otp_invalido" });
  await api.confirmarCorreo({ correo: "nuevo@example.test", codigo: "123456" });
  assert.equal((await api.perfil()).correo, "nuevo@example.test");
});

test("recuperación de contraseña y seguridad", async () => {
  const { api } = await cargarCliente("?simulado=1");
  await api.recuperar({ dni: "45678912" });
  await api.restablecer({ uid: "u", token: "t", clave: "NuevaClave2026" });
  await entrarConClave(api, "NuevaClave2026");
  assert.ok(Array.isArray(await api.accesos()));
  assert.ok(Array.isArray(await api.dispositivos()));
});

// ------------------------------------------------------------- Modo real (con fetch falso)

test("modo real: traduce los formularios a los cuerpos de C4 con CSRF e idempotencia", async () => {
  globalThis.document = { cookie: "csrftoken=token-de-prueba" };
  const peticiones = [];
  globalThis.fetch = async (url, opciones) => {
    peticiones.push({ url, metodo: opciones.method, cabeceras: opciones.headers,
                      cuerpo: opciones.body ? JSON.parse(opciones.body) : undefined });
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const { api, simulado } = await cargarCliente("");
  assert.equal(simulado, false);
  await api.ingresar({ dni: "12345678", clave: "secreta" });
  await api.confirmarOtp({ codigo: "123456", recordar: true });
  await api.activarIdentidad({ dni: "12345678", tarjeta: "123456782", codigo: "K7Q2M9XA" });
  await api.activarCorreo({ token: "t", correo: "a@example.test" });
  await api.activarConfirmar({ token: "t", codigo: "123456", clave: "NuevaClave2026" });
  await api.recuperar({ dni: "12345678" });
  await api.restablecer({ uid: "u", token: "t", clave: "NuevaClave2026" });
  await api.bloquear({ id: 1, motivo: "robo", clave: "secreta" });
  await api.confirmarCorreo({ correo: "a@example.test", codigo: "123456" });
  await api.crearOrden({ monto: 500, idempotencia: "uuid-de-prueba" });
  assert.deepEqual(peticiones.map((p) => p.cuerpo), [
    { documento: "12345678", contrasena: "secreta" }, { otp: "123456", recordar_dispositivo: true },
    { documento: "12345678", numero_tarjeta: "123456782", codigo: "K7Q2M9XA" },
    { token_activacion: "t", correo: "a@example.test" },
    { token_activacion: "t", otp: "123456", contrasena: "NuevaClave2026" },
    { documento: "12345678" }, { uid: "u", token: "t", contrasena: "NuevaClave2026" },
    { motivo: "robo", contrasena: "secreta" }, { correo: "a@example.test", otp: "123456" },
    { monto: 500 },
  ]);
  assert.equal(peticiones[7].url, "/api/v1/tarjetas/1/bloquear/");
  assert.equal(peticiones[9].url, "/api/v1/recargas/ordenes/");
  assert.equal(peticiones[9].cabeceras["Idempotency-Key"], "uuid-de-prueba");
  for (const p of peticiones) assert.equal(p.cabeceras["X-CSRFToken"], "token-de-prueba");
});

test("modo real: sesion() es false sin sesión y propaga otros errores", async () => {
  globalThis.document = { cookie: "" };
  const { api } = await cargarCliente("");
  globalThis.fetch = async () => ({ ok: false, status: 401, json: async () => ({ error: { codigo: "no_autenticado", mensaje: "Inicia sesión" } }) });
  assert.equal(await api.sesion(), false);
  globalThis.fetch = async () => ({ ok: false, status: 500, json: async () => ({}) });
  await assert.rejects(api.sesion(), { codigo: "servidor" });
});

test("modo real: reenviarOtp usa la extensión de C4 con CSRF", async () => {
  globalThis.document = { cookie: "csrftoken=abc" };
  const { api } = await cargarCliente("");
  let visto;
  globalThis.fetch = async (url, o) => { visto = { url, o }; return { ok: true, status: 202, json: async () => ({}) }; };
  await api.reenviarOtp();
  assert.equal(visto.url, "/api/v1/sesion/otp/reenviar/");
  assert.equal(visto.o.method, "POST");
  assert.equal(visto.o.headers["X-CSRFToken"], "abc");
  globalThis.fetch = async () => ({ ok: false, status: 429, json: async () => ({ error: { codigo: "bloqueo_temporal", mensaje: "Espera", reintentar_en: 30 } }) });
  await assert.rejects(api.reenviarOtp(), (e) => e.codigo === "bloqueo_temporal" && e.reintentar_en === 30);
});

// ------------------------------------------------------------- intentos.js

test("la clave de compra sobrevive una recarga de página y se cierra solo con su orden", async () => {
  const { leerIntento, prepararIntento, guardarOrden, cerrarIntento, descartarIntento } =
    await import(new URL("intentos.js", DIR_JS));
  const almacen = memoria();
  const primero = prepararIntento(almacen, "compra", 1000, () => "uuid-uno");
  assert.equal(prepararIntento(almacen, "compra", 1000, () => "uuid-dos").clave, primero.clave);
  guardarOrden(almacen, "compra", primero, "orden-uno");
  cerrarIntento(almacen, "compra", "otra-orden");
  assert.equal(leerIntento(almacen, "compra").clave, "uuid-uno");
  cerrarIntento(almacen, "compra", "orden-uno");
  assert.equal(leerIntento(almacen, "compra"), null);
  assert.equal(prepararIntento(almacen, "compra", 2000, () => "uuid-dos").clave, "uuid-dos");
  descartarIntento(almacen, "compra");
  assert.equal(leerIntento(almacen, "compra"), null);
});
