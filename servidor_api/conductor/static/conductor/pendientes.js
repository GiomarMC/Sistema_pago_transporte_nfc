const BASE = 'subepe-conductor-v1';
const TABLA = 'paquetes';

export function clavePaquete(paquete) {
  const v = new DataView(paquete.buffer, paquete.byteOffset, paquete.byteLength);
  if (paquete.length < 20 || paquete[0] !== 0x53 || paquete[1] !== 0x50) {
    throw new Error('Paquete C1 inválido.');
  }
  return `${v.getUint16(4, true)}:${v.getUint32(8, true)}:${paquete[3]}`;
}

function solicitud(peticion) {
  return new Promise((resolve, reject) => {
    peticion.onsuccess = () => resolve(peticion.result);
    peticion.onerror = () => reject(peticion.error);
  });
}

async function base() {
  const peticion = indexedDB.open(BASE, 1);
  peticion.onupgradeneeded = () => {
    if (!peticion.result.objectStoreNames.contains(TABLA)) {
      peticion.result.createObjectStore(TABLA, {keyPath: 'clave'});
    }
  };
  return solicitud(peticion);
}

function fin(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error('No se pudo guardar la cola.'));
  });
}

export async function guardarPaquetes(paquetes) {
  const db = await base();
  try {
    const tx = db.transaction(TABLA, 'readwrite');
    const tabla = tx.objectStore(TABLA);
    for (const paquete of paquetes) tabla.put({clave: clavePaquete(paquete), datos: paquete.slice().buffer,
      creado: Date.now()});
    await fin(tx);
  } finally { db.close(); }
}

export async function pendientes() {
  const db = await base();
  try {
    const tx = db.transaction(TABLA, 'readonly');
    const filas = await solicitud(tx.objectStore(TABLA).getAll());
    return filas.sort((a, b) => a.creado - b.creado);
  } finally { db.close(); }
}

export async function quitar(clave) {
  const db = await base();
  try {
    const tx = db.transaction(TABLA, 'readwrite');
    tx.objectStore(TABLA).delete(clave);
    await fin(tx);
  } finally { db.close(); }
}

// El servidor conserva el ACUSE para la próxima conexión del validador.
export async function subirPendientes(enviar) {
  let subidos = 0;
  for (const fila of await pendientes()) {
    await enviar(new Uint8Array(fila.datos));
    await quitar(fila.clave);
    subidos++;
  }
  return subidos;
}
