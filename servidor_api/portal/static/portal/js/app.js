import {api, simulado} from './cliente.js';

export const $ = selector => document.querySelector(selector);
export const soles = centimos => `S/ ${Number(centimos / 100).toFixed(2)}`;
export const fecha = iso => new Intl.DateTimeFormat('es-PE', {dateStyle: 'medium', timeStyle: 'short'}).format(new Date(iso));
export const escapar = dato => String(dato ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function ruta(pagina = '', query = {}) {
  const base = document.body.dataset.base;
  const url = new URL(base + pagina, location.origin);
  if (simulado) url.searchParams.set('simulado', '1');
  for (const [clave, valor] of Object.entries(query)) if (valor != null) url.searchParams.set(clave, valor);
  return url.pathname + url.search;
}
export function navegar(pagina = '', query = {}) { location.assign(ruta(pagina, query)); }
export function aviso(mensaje, error = false) {
  const el = $('#aviso'); el.textContent = mensaje; el.classList.toggle('error', error); el.hidden = false;
  clearTimeout(aviso.timer); aviso.timer = setTimeout(() => { el.hidden = true; }, 5000);
}
export function confirmar(titulo, texto, etiqueta = 'Confirmar') {
  const dialogo = $('#confirmacion');
  $('#confirmacion-titulo').textContent = titulo;
  $('#confirmacion-texto').textContent = texto;
  dialogo.querySelector('[value=confirmar]').textContent = etiqueta;
  dialogo.showModal();
  return new Promise(resolve => dialogo.addEventListener('close', () => resolve(dialogo.returnValue === 'confirmar'), {once: true}));
}
export function error(exc, destino) {
  if (exc.codigo === 'no_autenticado') { navegar('ingresar/'); return; }
  if (destino) { destino.textContent = exc.message || 'No se pudo completar la operación.'; destino.hidden = false; }
  else aviso(exc.message || 'No se pudo completar la operación.', true);
}
export function claveValida(clave) { return clave.length >= 10 && /[A-Z]/.test(clave) && /[a-z]/.test(clave) && /\d/.test(clave); }
export function luhn(numero) {
  let suma = 0, duplicar = false;
  for (let i = numero.length - 1; i >= 0; i--) { let n = Number(numero[i]); if (duplicar) { n *= 2; if (n > 9) n -= 9; } suma += n; duplicar = !duplicar; }
  return suma % 10 === 0;
}
export function movimientoHTML(m, mostrarSaldo = false) {
  const positivo = m.monto >= 0;
  const iconos = {viaje: '↗', recarga: '+', emision: '▣', ajuste: '±'};
  return `<li class="movimiento"><span class="icono-movimiento" aria-hidden="true">${iconos[m.tipo] || '•'}</span><span class="movimiento-principal"><strong>${escapar(m.descripcion)}</strong><small>${escapar(fecha(m.fecha))}${m.validador ? ' · ' + escapar(m.validador) : ''}</small>${mostrarSaldo ? `<small>Saldo final: ${soles(m.saldo_final)}</small>` : ''}</span><span class="${positivo ? 'monto-positivo' : 'monto-negativo'}">${positivo ? '+' : '−'}${soles(Math.abs(m.monto))}</span></li>`;
}
for (const enlace of document.querySelectorAll('[data-nav]')) {
  const url = new URL(enlace.href);
  if (simulado) url.searchParams.set('simulado', '1');
  enlace.href = url.pathname + url.search;
  if (enlace.dataset.seccion === document.body.dataset.pagina) enlace.setAttribute('aria-current', 'page');
}
if (simulado) $('#modo-prueba').hidden = false;
if (document.body.dataset.publica === '0') {
  api.sesion().then(activa => { if (!activa) navegar('ingresar/'); }).catch(exc => error(exc));
}
