import {SERVICIO, INFO, SALIDA, ENTRADA, CONTROL, Reensamblador,
  datosInfo, separarPaquetes, unirPaquetes, fragmentar} from './protocolo.js';
import {guardarPaquetes, pendientes, subirPendientes} from './pendientes.js';

const boton = document.querySelector('#conectar');
const estado = document.querySelector('#estado');
const detalle = document.querySelector('#detalle');
const barra = document.querySelector('#progreso');
const almacenados = document.querySelector('#almacenados');
let ocupado = false;
let subiendo = false;

function mostrar(texto, descripcion = '') {
  estado.textContent = texto;
  detalle.textContent = descripcion;
}
function csrf() {
  return document.cookie.split('; ').find(p => p.startsWith('csrftoken='))?.split('=')[1] || '';
}
function espera(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

async function enviar(paquetes) {
  const respuestas = [];
  let lote = [], tam = 0;
  async function subir() {
    if (!lote.length) return;
    let respuesta;
    try {
      respuesta = await fetch('/api/v2/paquetes/', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/octet-stream', 'Accept': 'application/octet-stream',
          'X-CSRFToken': csrf()},
        body: unirPaquetes(lote),
      });
    } catch (error) {
      error.red = true;
      throw error;
    }
    if (!respuesta.ok) {
      const error = new Error(respuesta.status === 404 ?
        'El servidor aún no ofrece sincronización por Bluetooth.' :
        `El servidor rechazó los paquetes (HTTP ${respuesta.status}).`);
      error.red = respuesta.status >= 500;
      throw error;
    }
    respuestas.push(...separarPaquetes(await respuesta.arrayBuffer()));
    lote = []; tam = 0;
  }
  for (const paquete of paquetes) {
    if (tam + paquete.length > 60000) await subir();
    lote.push(paquete); tam += paquete.length;
  }
  await subir();
  return respuestas;
}

async function actualizarPendientes() {
  const n = (await pendientes()).length;
  almacenados.textContent = n ? `${n} paquetes guardados en este celular para subir después.` : '';
}

async function reintentarGuardados() {
  if (!navigator.onLine || ocupado || subiendo) return;
  subiendo = true;
  try {
    const n = await subirPendientes(async paquete => { await enviar([paquete]); });
    await actualizarPendientes();
    if (n) mostrar(`${n} paquetes guardados subidos`, 'Los acuses llegarán al bus en su próxima sincronización.');
  } catch (error) {
    mostrar('Hay paquetes pendientes', error.message);
  } finally { subiendo = false; }
}

async function sincronizar() {
  if (ocupado) return;
  if (!navigator.bluetooth) { mostrar('Usa Chrome en Android', 'Este navegador no admite Web Bluetooth.'); return; }
  ocupado = true;
  boton.disabled = true;
  let dispositivo, control;
  try {
    // requestDevice se llama directamente desde el clic: Web Bluetooth exige ese gesto.
    dispositivo = await navigator.bluetooth.requestDevice({filters: [{services: [SERVICIO]}]});
    mostrar('Conectando con el validador…');
    const servidor = await dispositivo.gatt.connect();
    const servicio = await servidor.getPrimaryService(SERVICIO);
    const info = datosInfo(await (await servicio.getCharacteristic(INFO)).readValue());
    const salida = await servicio.getCharacteristic(SALIDA);
    const entrada = await servicio.getCharacteristic(ENTRADA);
    control = await servicio.getCharacteristic(CONTROL);
    mostrar(`Bus V${info.bus} · ${info.pendientes} pendientes`, 'Recibiendo paquetes firmados del validador.');
    const reensamblador = new Reensamblador();
    const paquetes = [];
    let errorTrama = null;
    let ultimo = Date.now();
    salida.addEventListener('characteristicvaluechanged', evento => {
      try {
        ultimo = Date.now();
        const paquete = reensamblador.recibir(evento.target.value);
        if (paquete) {
          paquetes.push(paquete);
          barra.value = Math.min(90, 10 + paquetes.length * 8);
          detalle.textContent = `${paquetes.length} paquetes recibidos`;
        }
      } catch (error) { errorTrama = error; detalle.textContent = error.message; }
    });
    await salida.startNotifications();
    await control.writeValueWithResponse(new Uint8Array([1]));
    const comienzo = Date.now();
    while (Date.now() - comienzo < 15000) {
      await espera(200);
      if (paquetes.length && Date.now() - ultimo >= 1400) break;
    }
    if (!paquetes.length) throw new Error('El validador no envió paquetes. Acércate y vuelve a intentar.');
    if (errorTrama) throw errorTrama;
    if (reensamblador.actual) throw new Error('Llegó un paquete incompleto. Vuelve a intentar.');
    const viajes = paquetes.reduce((n, p) => n + (p[3] === 1 ? p[20] : 0), 0);
    let respuestas;
    try { respuestas = await enviar(paquetes); }
    catch (error) {
      if (!error.red && navigator.onLine) throw error;
      await guardarPaquetes(paquetes);
      await actualizarPendientes();
      mostrar(`Guardados ${paquetes.length} paquetes`, 'Se subirán al recuperar los datos móviles.');
      return;
    }
    let id = crypto.getRandomValues(new Uint32Array(1))[0];
    for (const respuesta of respuestas) {
      for (const trama of fragmentar(respuesta, id++)) await entrada.writeValueWithResponse(trama);
    }
    barra.value = 100;
    const listas = respuestas.filter(p => p[3] === 0x82);
    const version = listas.length ? new DataView(listas.at(-1).buffer).getUint32(24, true) : info.versionListas;
    mostrar(`${viajes} viajes subidos`, `Listas actualizadas a v${version}.`);
  } catch (error) {
    mostrar('No se pudo sincronizar', error.message || 'Inténtalo de nuevo.');
  } finally {
    if (control && dispositivo?.gatt?.connected) {
      try { await control.writeValueWithResponse(new Uint8Array([3])); } catch (_) { /* la sesión pudo caer */ }
    }
    dispositivo?.gatt?.disconnect();
    ocupado = false;
    boton.disabled = false;
  }
}

boton?.addEventListener('click', sincronizar);
window.addEventListener('online', reintentarGuardados);
actualizarPendientes().then(reintentarGuardados).catch(error => mostrar('Almacenamiento no disponible', error.message));
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/conductor/sw.js', {scope: '/conductor/'}).catch(() => {});
