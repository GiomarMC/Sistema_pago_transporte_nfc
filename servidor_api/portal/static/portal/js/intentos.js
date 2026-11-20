// Conserva la clave de una compra durante recargas de página y caídas de red.
export function leerIntento(almacen, llave) {
  try { return JSON.parse(almacen.getItem(llave)); } catch { return null; }
}
export function prepararIntento(almacen, llave, monto, nuevoId = () => crypto.randomUUID()) {
  const anterior = leerIntento(almacen, llave);
  if (anterior?.monto === monto && anterior.clave) return anterior;
  const intento = {clave: nuevoId(), monto};
  try { almacen.setItem(llave, JSON.stringify(intento)); } catch { /* Sigue en memoria mientras la página esté abierta. */ }
  return intento;
}
export function guardarOrden(almacen, llave, intento, orden) {
  try { almacen.setItem(llave, JSON.stringify({...intento, orden})); } catch { /* La clave sigue en memoria. */ }
}
export function cerrarIntento(almacen, llave, orden) {
  if (leerIntento(almacen, llave)?.orden === orden) try { almacen.removeItem(llave); } catch { /* Sin almacenamiento disponible. */ }
}
export function descartarIntento(almacen, llave) {
  try { almacen.removeItem(llave); } catch { /* Sin almacenamiento disponible. */ }
}
