import {api, simulado} from './cliente.js';
import {$, soles, fecha, escapar, ruta, navegar, aviso, confirmar, error, claveValida, luhn, movimientoHTML} from './app.js';
import {leerIntento, prepararIntento, guardarOrden, cerrarIntento} from './intentos.js';

const pagina = document.body.dataset.pagina;
const poner = (selector, dato) => { $(selector).textContent = dato; };
const mostrarError = exc => error(exc, $('#error'));
const claveCompra = `subepe.portal.recarga.intento.${simulado ? 'demo' : 'real'}`;
const almacenCompra = (() => { try { return sessionStorage; } catch { return {getItem: () => null, setItem: () => {}, removeItem: () => {}}; } })();

if (pagina === 'ingresar') {
  $('#credenciales-demo').hidden = !simulado;
  const params = new URLSearchParams(location.search);
  const codigo = params.get('error');
  if (codigo) { $('#error-ingreso').textContent = ({credenciales_invalidas:'DNI o contraseña incorrectos.', bloqueo_temporal:'Cuenta bloqueada temporalmente.', no_autenticado:'Ingresa para continuar.', validacion:'Revisa los datos ingresados.'})[codigo] || 'No se pudo iniciar sesión.'; $('#error-ingreso').hidden = false; }
  if (codigo === 'bloqueo_temporal') cuentaRegresiva(Math.min(1800, Number(params.get('reintentar_en')) || 60));
  if (['otp_invalido', 'otp_expirado'].includes(codigo)) { $('#form-ingreso').hidden = true; $('#form-otp').hidden = false; $('#error-ingreso').hidden = true; $('#error-otp').textContent = codigo === 'otp_expirado' ? 'El código expiró. Solicita uno nuevo.' : 'El código no es correcto.'; $('#error-otp').hidden = false; iniciarReenvio(); }
  $('#ver-clave').onclick = () => { const campo = $('#clave'); campo.type = campo.type === 'password' ? 'text' : 'password'; $('#ver-clave').textContent = campo.type === 'password' ? 'Mostrar' : 'Ocultar'; };
  $('#form-ingreso').onsubmit = async ev => {
    ev.preventDefault(); $('#error-ingreso').hidden = true;
    try { const r = await api.ingresar({dni: $('#dni').value, clave: $('#clave').value}); if (!r.requiere_otp) { navegar(); return; } $('#form-ingreso').hidden = true; $('#form-otp').hidden = false; iniciarReenvio(); $('#otp').focus(); }
    catch (exc) { error(exc, $('#error-ingreso')); if (exc.codigo === 'bloqueo_temporal') cuentaRegresiva(exc.reintentar_en || 60); }
  };
  function cuentaRegresiva(segundos) {
    const boton = $('#form-ingreso button[type=submit], #form-ingreso button:not([type])'); boton.disabled = true;
    const tick = () => { if (segundos-- > 0) { $('#error-ingreso').textContent = `Acceso bloqueado. Reintenta en ${segundos + 1} s.`; setTimeout(tick, 1000); } else boton.disabled = false; }; tick();
  }
  function iniciarReenvio() {
    let segundos = 60; const boton = $('#reenviar'); boton.disabled = true;
    const tick = () => { if (segundos-- > 0) { boton.textContent = `Reenviar código (${segundos + 1} s)`; setTimeout(tick, 1000); } else { boton.disabled = false; boton.textContent = 'Reenviar código'; } }; tick();
  }
  $('#reenviar').onclick = async () => { try { await api.reenviarOtp(); aviso('Código reenviado.'); iniciarReenvio(); } catch (exc) { error(exc, $('#error-otp')); } };
  $('#form-otp').onsubmit = async ev => { ev.preventDefault(); try { await api.confirmarOtp({codigo: $('#otp').value, recordar: $('#recordar').checked}); navegar(); } catch (exc) { error(exc, $('#error-otp')); } };
}

if (pagina === 'activar') {
  $('#datos-demo').hidden = !simulado;
  $('#otp-demo').hidden = !simulado;
  let token = null;
  const pasos = document.querySelectorAll('.pasos li');
  const avanzar = n => { for (let i = 1; i <= 3; i++) $(`#paso-${i}`).hidden = i !== n; pasos.forEach((el, i) => el.classList.toggle('actual', i === n - 1)); poner('#progreso', `Paso ${n} de 3`); };
  $('#clave').oninput = () => { const clave = $('#clave').value; const puntos = [clave.length >= 10, /[A-Z]/.test(clave), /[a-z]/.test(clave), /\d/.test(clave)].filter(Boolean).length; $('#fortaleza').value = puntos; poner('#fortaleza-texto', puntos === 4 ? 'Contraseña fuerte: cumple todas las reglas' : puntos >= 2 ? 'Fortaleza media: faltan reglas' : 'Contraseña débil'); };
  $('#numero').oninput = () => { const v = $('#numero').value.replace(/\D/g, '').slice(0, 9); $('#numero').value = v.replace(/(\d{4})(?=\d)/g, '$1 ').trim(); };
  $('#codigo').oninput = () => { const v = $('#codigo').value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8); $('#codigo').value = v.replace(/^(.{4})(?=.)/, '$1-'); };
  $('#paso-1').onsubmit = async ev => { ev.preventDefault(); const numero = $('#numero').value.replace(/\s/g, ''); const codigo = $('#codigo').value.replace('-', ''); if (!/^\d{9}$/.test(numero) || !luhn(numero)) { $('#error').textContent = 'El número de tarjeta no supera la verificación. Revisa los 9 dígitos.'; $('#error').hidden = false; return; } try { const respuesta = await api.activarIdentidad({dni: $('#dni').value, tarjeta: numero, codigo}); token = respuesta.token_activacion; $('#error').hidden = true; avanzar(2); } catch (exc) { mostrarError(exc); } };
  $('#paso-2').onsubmit = async ev => { ev.preventDefault(); try { await api.activarCorreo({token, correo: $('#correo').value}); $('#error').hidden = true; avanzar(3); } catch (exc) { mostrarError(exc); } };
  $('#paso-3').onsubmit = async ev => { ev.preventDefault(); if (!claveValida($('#clave').value)) { $('#error').textContent = 'La contraseña debe tener 10 caracteres, mayúscula, minúscula y número.'; $('#error').hidden = false; return; } try { await api.activarConfirmar({token, codigo: $('#otp').value, clave: $('#clave').value}); navegar(); } catch (exc) { mostrarError(exc); } };
}

if (pagina === 'inicio') {
  async function cargar() {
    try {
      const [r, mov] = await Promise.all([api.resumen(), api.movimientos({pagina: 1})]);
      poner('#nombre', r.cliente.nombre.split(' ')[0]); poner('#saldo', soles(r.cuenta.saldo));
      poner('#tarifa', `${r.cuenta.tarifa.nombre} · ${soles(r.cuenta.tarifa.precio)}`);
      poner('#viajes', r.cuenta.saldo > 0 && r.cuenta.tarifa.precio > 0 ? `≈ ${Math.floor(r.cuenta.saldo / r.cuenta.tarifa.precio)} viajes disponibles` : 'Sin viajes disponibles. Puedes recargar saldo.');
      const t = r.tarjetas[0]; poner('#tarjeta', t ? `•••• ${t.numero_impreso.slice(-4)}` : 'Sin tarjeta'); poner('#estado', t?.estado || 'Sin tarjeta');
      $('#pendiente').hidden = !r.cuenta.por_cargar_en_tarjeta;
      if (r.cuenta.por_cargar_en_tarjeta) poner('#pendiente', `${soles(r.cuenta.por_cargar_en_tarjeta)} se cargarán en tu tarjeta en tu próximo viaje.`);
      $('#bloquear').hidden = t?.estado !== 'activa';
      $('#movimientos').innerHTML = mov.resultados.length ? mov.resultados.slice(0, 5).map(movimientoHTML).join('') : '<li>Aún no hay movimientos.</li>';
    } catch (exc) { mostrarError(exc); }
  }
  $('#bloquear').onclick = () => navegar('tarjeta/', {bloquear: '1'});
  $('#actualizar').onclick = cargar;
  cargar();
}

if (pagina === 'movimientos') {
  let paginaActual = 0, paginas = 1, acumulados = [], cargando = false, reinicioPendiente = false;
  function pintar() {
    if (!acumulados.length) { $('#lista').textContent = 'No hay movimientos para los filtros elegidos.'; return; }
    const grupos = new Map();
    for (const m of acumulados) { const dia = m.fecha.slice(0, 10); if (!grupos.has(dia)) grupos.set(dia, []); grupos.get(dia).push(m); }
    $('#lista').innerHTML = [...grupos].map(([dia, lista]) => `<section class="grupo-dia"><h2>${escapar(new Intl.DateTimeFormat('es-PE', {dateStyle:'long'}).format(new Date(dia + 'T12:00:00')))}</h2><ul class="lista">${lista.map(m => movimientoHTML(m, true)).join('')}</ul></section>`).join('');
  }
  async function cargar(reiniciar = false) {
    if (cargando) { if (reiniciar) reinicioPendiente = true; return; }
    if (reiniciar) { paginaActual = 0; paginas = 1; acumulados = []; }
    if (paginaActual >= paginas) return;
    cargando = true; $('#cargar-mas').disabled = true; $('#error').hidden = true;
    if (!acumulados.length) $('#lista').textContent = 'Cargando movimientos…';
    try {
      const r = await api.movimientos({tipo: $('#tipo').value, desde: $('#desde').value, hasta: $('#hasta').value, pagina: paginaActual + 1});
      paginaActual = r.pagina; paginas = r.paginas; acumulados.push(...r.resultados); pintar();
      $('#cargar-mas').hidden = paginaActual >= paginas; poner('#pagina', `Página ${paginaActual} de ${paginas}`);
    } catch (exc) { mostrarError(exc); if (!acumulados.length) $('#lista').textContent = 'No se pudieron cargar los movimientos.'; }
    finally { cargando = false; $('#cargar-mas').disabled = false; if (reinicioPendiente) { reinicioPendiente = false; cargar(true); } }
  }
  $('#filtros').onsubmit = ev => { ev.preventDefault(); cargar(true); };
  $('#cargar-mas').onclick = () => cargar();
  cargar();
}

if (pagina === 'recargar') {
  const compraAnterior = leerIntento(almacenCompra, claveCompra);
  let claveIntento = compraAnterior?.clave || null, montoIntento = compraAnterior?.monto || null;
  const monto = () => Math.round(Number($('#monto').value) * 100);
  for (const radio of document.querySelectorAll('[name=rapido]')) radio.onchange = () => { $('#monto').value = radio.value; actualizar(); };
  const actualizar = () => { poner('#resumen', `Monto a pagar: ${soles(Number.isFinite(monto()) ? monto() : 0)}`); if (monto() !== montoIntento) claveIntento = null; };
  $('#monto').oninput = actualizar;
  if (compraAnterior?.monto) { $('#monto').value = (compraAnterior.monto / 100).toFixed(2); actualizar(); }
  $('#form-recarga').onsubmit = async ev => {
    ev.preventDefault(); const centimos = monto();
    if (!Number.isInteger(centimos) || centimos < 100 || centimos > 20000) { $('#error').textContent = 'El monto debe estar entre S/ 1.00 y S/ 200.00.'; $('#error').hidden = false; return; }
    if (!await confirmar('Confirmar recarga', `Se creará una orden de ${soles(centimos)}. El saldo se acredita solo tras la confirmación del pago.`, 'Ir al pago')) return;
    const boton = $('#continuar'); boton.disabled = true; $('#error').hidden = true;
    const intento = claveIntento && montoIntento === centimos ? {clave: claveIntento, monto: montoIntento} : prepararIntento(almacenCompra, claveCompra, centimos);
    claveIntento = intento.clave; montoIntento = centimos;
    try {
      const orden = await api.crearOrden({monto: centimos, idempotencia: claveIntento});
      guardarOrden(almacenCompra, claveCompra, intento, orden.id);
      if (simulado) { navegar('recargar/resultado/', {orden: orden.id}); return; }
      if (!orden.url_pago) throw new Error('La orden no tiene una URL de pago válida.');
      const url = new URL(orden.url_pago, location.origin);
      const httpLocal = ['localhost', '127.0.0.1'].includes(location.hostname) && url.origin === location.origin && url.protocol === 'http:';
      if (url.protocol !== 'https:' && !httpLocal) throw new Error('URL de pago inválida.');
      location.assign(url.href);
    } catch (exc) { mostrarError(exc); boton.disabled = false; }
  };
}

if (pagina === 'resultado') {
  const id = new URLSearchParams(location.search).get('orden');
  let consultas = 0;
  async function consultar() {
    if (!id) { poner('#estado', 'No se indicó una orden.'); return; }
    try {
      const o = await api.orden(id); consultas++;
      if (o.estado === 'acreditada') {
        cerrarIntento(almacenCompra, claveCompra, id);
        poner('#estado', `Pago confirmado: ${soles(o.monto)} acreditados en tu cuenta.`);
        poner('#detalle', 'La carga en la tarjeta se aplicará cuando la acerques a un validador.'); return;
      }
      if (['fallida', 'rechazada', 'expirada', 'reembolsada'].includes(o.estado)) {
        cerrarIntento(almacenCompra, claveCompra, id);
        poner('#estado', o.estado === 'expirada' ? 'La orden expiró.' : o.estado === 'reembolsada' ? 'La recarga fue reembolsada.' : 'El pago no fue aprobado.');
        $('#reintentar').hidden = false; return;
      }
      if (consultas >= 30) { poner('#estado', 'Estamos confirmando tu pago, te avisaremos por correo.'); return; }
      poner('#estado', 'Esperando confirmación del proveedor de pagos…');
      setTimeout(consultar, 2000);
    } catch (exc) { mostrarError(exc); }
  }
  consultar();
}

if (pagina === 'tarjeta') {
  let tarjetaId = null, estadoTarjeta = null;
  async function cargar() {
    try {
      const [r, solicitudes] = await Promise.all([api.resumen(), api.reposiciones()]);
      const t = r.tarjetas[0]; tarjetaId = t?.id; estadoTarjeta = t?.estado;
      poner('#numero', t ? `•••• •••• ${t.numero_impreso.slice(-1)}` : 'Sin tarjeta');
      poner('#estado', t?.estado || 'Sin tarjeta'); $('#estado').className = `estado ${t?.estado || ''}`;
      poner('#emision', t?.emitida ? `Emitida: ${new Intl.DateTimeFormat('es-PE', {dateStyle: 'medium', timeZone: 'UTC'}).format(new Date(t.emitida))}` : '');
      poner('#ultimo', t?.ultimo_uso ? `Último uso: ${fecha(t.ultimo_uso)}` : 'Sin uso reciente');
      $('#mostrar-bloqueo').hidden = estadoTarjeta !== 'activa';
      if (estadoTarjeta !== 'activa') $('#bloqueo').hidden = true;
      const s = solicitudes[0];
      poner('#reposicion', s ? `Solicitud ${s.estado}. ${s.estado === 'lista' ? 'Puedes recogerla con tu DNI.' : s.estado === 'entregada' ? 'Tarjeta entregada.' : 'Te avisaremos cuando esté lista.'}` : estadoTarjeta === 'bloqueada' ? 'Aún no tienes una solicitud.' : 'Disponible después de bloquear la tarjeta.');
      $('#solicitar').hidden = estadoTarjeta !== 'bloqueada' || Boolean(s);
    } catch (exc) { mostrarError(exc); }
  }
  $('#mostrar-bloqueo').onclick = () => { $('#bloqueo').hidden = false; $('#mostrar-bloqueo').hidden = true; $('#motivo').focus(); };
  if (new URLSearchParams(location.search).get('bloquear') === '1') $('#bloqueo').hidden = false;
  $('#form-bloqueo').onsubmit = async ev => {
    ev.preventDefault();
    try { await api.bloquear({id: tarjetaId, motivo: $('#motivo').value, clave: $('#clave').value}); aviso('Tarjeta bloqueada.'); cargar(); }
    catch (exc) { mostrarError(exc); }
  };
  $('#solicitar').onclick = async () => { try { await api.solicitarReposicion({tarjeta_id: tarjetaId}); aviso('Reposición solicitada.'); cargar(); } catch (exc) { mostrarError(exc); } };
  cargar();
}

if (pagina === 'seguridad') {
  async function cargar() {
    try {
      const [accesos, dispositivos] = await Promise.all([api.accesos(), api.dispositivos()]);
      $('#accesos').innerHTML = accesos.length ? accesos.map(a => { const correcto = a.exito ?? a.resultado === 'correcto'; const detalle = a.descripcion || [a.dispositivo, a.ip].filter(Boolean).join(' · '); return `<li class="${correcto ? '' : 'monto-negativo'}">${escapar(fecha(a.fecha))} · ${escapar(detalle)} (${correcto ? 'correcto' : 'fallido'})</li>`; }).join('') : '<li>Sin accesos recientes.</li>';
      $('#dispositivos').innerHTML = dispositivos.length ? dispositivos.map(d => `<li class="fila"><span>${escapar(d.nombre)}${d.actual ? ' (este dispositivo)' : ''}</span>${d.actual ? '' : `<button class="boton secundario" data-quitar="${escapar(d.id)}" aria-label="Quitar ${escapar(d.nombre)}">Quitar</button>`}</li>`).join('') : '<li>Sin dispositivos de confianza.</li>';
    } catch (exc) { mostrarError(exc); }
  }
  $('#dispositivos').onclick = async ev => {
    const id = ev.target.dataset.quitar; if (!id) return;
    if (!await confirmar('Quitar dispositivo', 'Dejará de ser un dispositivo de confianza.', 'Quitar')) return;
    try { await api.quitarDispositivo({id}); cargar(); } catch (exc) { mostrarError(exc); }
  };
  $('#form-clave').onsubmit = async ev => {
    ev.preventDefault();
    if (!claveValida($('#nueva').value)) { $('#error').textContent = 'La nueva contraseña debe tener 10 caracteres, mayúscula, minúscula y número.'; $('#error').hidden = false; return; }
    try { await api.cambiarClave({actual: $('#actual').value, nueva: $('#nueva').value}); $('#form-clave').reset(); $('#error').hidden = true; aviso('Contraseña actualizada.'); }
    catch (exc) { mostrarError(exc); }
  };
  $('#salir').onclick = async () => { try { await api.salir(); navegar('ingresar/'); } catch (exc) { mostrarError(exc); } };
  cargar();
}

if (pagina === 'perfil') {
  let correoPendiente = null;
  async function cargar() {
    try { const p = await api.perfil(); poner('#nombre', p.nombre); poner('#dni', p.documento_enmascarado); poner('#correo', p.correo); }
    catch (exc) { mostrarError(exc); }
  }
  $('#form-correo').onsubmit = async ev => { ev.preventDefault(); try { correoPendiente = $('#nuevo-correo').value; await api.pedirCambioCorreo({correo: correoPendiente}); $('#form-otp').hidden = false; $('#otp').focus(); } catch (exc) { mostrarError(exc); } };
  $('#form-otp').onsubmit = async ev => { ev.preventDefault(); try { await api.confirmarCorreo({correo: correoPendiente, codigo: $('#otp').value}); $('#form-otp').hidden = true; $('#form-correo').reset(); aviso('Correo actualizado.'); cargar(); } catch (exc) { mostrarError(exc); } };
  $('#salir').onclick = async () => { try { await api.salir(); navegar('ingresar/'); } catch (exc) { mostrarError(exc); } };
  cargar();
}

if (pagina === 'recuperar') {
  $('#form-recuperar').onsubmit = async ev => {
    ev.preventDefault(); try { await api.recuperar({dni: $('#dni').value}); poner('#mensaje', 'Si el DNI está registrado, recibirás instrucciones en tu correo.'); if (simulado) { const a = document.createElement('a'); a.href = ruta('restablecer/', {uid: 'demo-uid', token: 'demo-token'}); a.textContent = 'Abrir enlace de recuperación de prueba'; $('#mensaje').append(a); } }
    catch (exc) { poner('#mensaje', exc.message); }
  };
}

if (pagina === 'restablecer') {
  $('#form-restablecer').onsubmit = async ev => {
    ev.preventDefault();
    if (!claveValida($('#nueva').value)) { poner('#mensaje', 'La contraseña debe tener 10 caracteres, mayúscula, minúscula y número.'); return; }
    const parametros = new URLSearchParams(location.search);
    try { await api.restablecer({uid: parametros.get('uid'), token: parametros.get('token'), clave: $('#nueva').value}); poner('#mensaje', 'Contraseña cambiada. Ya puedes ingresar.'); const a = document.createElement('a'); a.href = ruta('ingresar/'); a.textContent = 'Ingresar'; $('#mensaje').append(a); }
    catch (exc) { poner('#mensaje', exc.message); }
  };
}
