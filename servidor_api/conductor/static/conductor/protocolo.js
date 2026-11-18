// C1 5.1 y 5.10: la firma se valida en el servidor o el validador, nunca aquí.
export const SERVICIO = 'a3f1c000-5ab0-4e50-9e2b-53554245504e';
export const INFO = 'a3f1c001-5ab0-4e50-9e2b-53554245504e';
export const SALIDA = 'a3f1c002-5ab0-4e50-9e2b-53554245504e';
export const ENTRADA = 'a3f1c003-5ab0-4e50-9e2b-53554245504e';
export const CONTROL = 'a3f1c004-5ab0-4e50-9e2b-53554245504e';
const CARGA = 220;
const CABECERA = 13;

export function datosInfo(valor) {
  const vista = vistaDe(valor);
  if (vista.byteLength !== 34) throw new Error('Información BLE inválida.');
  return {bus: vista.getUint16(32, true), pendientes: vista.getUint16(12, true),
    versionListas: vista.getUint32(0, true)};
}

function vistaDe(valor) {
  if (valor instanceof DataView) return valor;
  if (valor instanceof Uint8Array) return new DataView(valor.buffer, valor.byteOffset, valor.byteLength);
  return new DataView(valor);
}

export function separarPaquetes(valor) {
  const vista = vistaDe(valor), paquetes = [];
  let pos = 0;
  while (pos < vista.byteLength) {
    if (vista.byteLength - pos < 20 || vista.getUint8(pos) !== 0x53 ||
        vista.getUint8(pos + 1) !== 0x50 || vista.getUint8(pos + 2) !== 1) {
      throw new Error('Respuesta C1 inválida.');
    }
    const origen = vista.getUint16(pos + 4, true);
    const largo = 20 + vista.getUint16(pos + 16, true) + (origen === 0 ? 64 : 16);
    if (largo > 2048 || pos + largo > vista.byteLength) throw new Error('Paquete C1 incompleto.');
    paquetes.push(new Uint8Array(vista.buffer, vista.byteOffset + pos, largo).slice());
    pos += largo;
  }
  return paquetes;
}

export function unirPaquetes(paquetes) {
  const largo = paquetes.reduce((n, p) => n + p.byteLength, 0);
  if (largo > 65536) throw new Error('Lote mayor de 64 KB.');
  const salida = new Uint8Array(largo);
  let pos = 0;
  for (const p of paquetes) { salida.set(p, pos); pos += p.byteLength; }
  return salida;
}

export function fragmentar(paquete, idMensaje, emisor = 0) {
  if (!paquete?.length || paquete.length > 2048) throw new Error('Paquete fuera de tamaño.');
  const total = Math.ceil(paquete.length / CARGA);
  const resultado = [];
  for (let indice = 0; indice < total; indice++) {
    const trozo = paquete.subarray(indice * CARGA, (indice + 1) * CARGA);
    const trama = new Uint8Array(CABECERA + trozo.length);
    const v = new DataView(trama.buffer);
    trama[0] = 69; trama[1] = 78; trama[2] = 2;
    v.setUint16(4, emisor, true); v.setUint32(6, idMensaje, true);
    trama[10] = indice; trama[11] = total; trama[12] = trozo.length;
    trama.set(trozo, CABECERA);
    resultado.push(trama);
  }
  return resultado;
}

export class Reensamblador {
  actual = null;
  recibir(valor, ahora = Date.now()) {
    const v = vistaDe(valor);
    if (v.byteLength < CABECERA || v.byteLength > 240 || v.getUint8(0) !== 69 ||
        v.getUint8(1) !== 78 || v.getUint8(2) !== 2 || v.getUint8(3) !== 0) {
      throw new Error('Fragmento BLE inválido.');
    }
    const emisor = v.getUint16(4, true), id = v.getUint32(6, true);
    const indice = v.getUint8(10), total = v.getUint8(11), n = v.getUint8(12);
    if (!total || total > 10 || indice >= total || !n || n > CARGA ||
        v.byteLength !== CABECERA + n || (indice + 1 < total && n !== CARGA)) {
      throw new Error('Fragmento BLE incompleto.');
    }
    const clave = `${emisor}:${id}`;
    if (!this.actual || this.actual.clave !== clave || ahora - this.actual.inicio > 3000) {
      this.actual = {clave, inicio: ahora, partes: Array(total), total};
    }
    if (this.actual.total !== total) throw new Error('Total de fragmentos inconsistente.');
    this.actual.partes[indice] = new Uint8Array(v.buffer, v.byteOffset + CABECERA, n).slice();
    if (this.actual.partes.filter(Boolean).length !== total) return null;
    const paquete = unirPaquetes(this.actual.partes);
    this.actual = null;
    const separados = separarPaquetes(paquete);
    if (separados.length !== 1) throw new Error('El fragmento no contiene un paquete C1.');
    return separados[0];
  }
}
