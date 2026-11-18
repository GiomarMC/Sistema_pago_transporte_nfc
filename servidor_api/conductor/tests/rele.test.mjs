import test from 'node:test';
import assert from 'node:assert/strict';
import {datosInfo, separarPaquetes, unirPaquetes, fragmentar, Reensamblador} from '../static/conductor/protocolo.js';
import {clavePaquete, guardarPaquetes, pendientes, subirPendientes} from '../static/conductor/pendientes.js';

function paquete(origen=103, cuerpo=32) {
  const p=new Uint8Array(20+cuerpo+(origen===0?64:16));
  const v=new DataView(p.buffer);
  p[0]=0x53;p[1]=0x50;p[2]=1;p[3]=2;
  v.setUint16(4,origen,true);v.setUint32(8,9,true);v.setUint16(16,cuerpo,true);
  return p;
}

test('C1 delimita paquetes y rechaza truncados', () => {
  const propios=paquete(), servidor=paquete(0,8);
  const juntos=unirPaquetes([propios,servidor]);
  assert.deepEqual(separarPaquetes(juntos),[propios,servidor]);
  assert.throws(()=>separarPaquetes(juntos.subarray(0,juntos.length-1)),/incompleto/);
  assert.equal(clavePaquete(propios),'103:9:2');
});

test('BLE fragmenta y reensambla fuera de orden con el formato C1', () => {
  const original=paquete(103,500), tramas=fragmentar(original,77,103);
  assert.equal(tramas.length,3);
  assert.equal(tramas[0].length,233);
  const r=new Reensamblador();
  assert.equal(r.recibir(tramas[2],0),null);
  assert.equal(r.recibir(tramas[0],1),null);
  assert.deepEqual(r.recibir(tramas[1],2),original);
});

test('BLE descarta la sesión parcial a los tres segundos', () => {
  const tramas=fragmentar(paquete(103,500),78,103),r=new Reensamblador();
  assert.equal(r.recibir(tramas[0],0),null);
  assert.equal(r.recibir(tramas[1],3001),null);
  assert.equal(r.recibir(tramas[2],3002),null);
  assert.throws(()=>r.recibir(tramas[2].subarray(0,20)),/incompleto/);
});

test('INFO contiene número de bus y pendientes', () => {
  const info=new Uint8Array(34),v=new DataView(info.buffer);
  v.setUint32(0,1234,true);v.setUint16(12,45,true);v.setUint16(32,103,true);
  assert.deepEqual(datosInfo(info),{bus:103,pendientes:45,versionListas:1234});
});

test('la cola local conserva lo no enviado y evita duplicados', async () => {
  const filas = new Map();
  globalThis.indexedDB = {open() {
    const peticion = {result: {
      objectStoreNames: {contains: () => false},
      createObjectStore() {}, close() {},
      transaction() {
        const tx = {objectStore() { return {
          put(fila) { filas.set(fila.clave, fila); setTimeout(() => tx.oncomplete?.(), 0); },
          getAll() { const r = {}; setTimeout(() => { r.result = [...filas.values()]; r.onsuccess?.(); }, 0); return r; },
          delete(clave) { filas.delete(clave); setTimeout(() => tx.oncomplete?.(), 0); },
        }; }};
        return tx;
      },
    }};
    setTimeout(() => { peticion.onupgradeneeded?.(); peticion.onsuccess?.(); }, 0);
    return peticion;
  }};
  const uno = paquete(), dos = paquete();
  new DataView(dos.buffer).setUint32(8, 10, true);
  await guardarPaquetes([uno, uno, dos]);
  assert.equal((await pendientes()).length, 2);
  let veces = 0;
  await assert.rejects(subirPendientes(async () => { if (++veces === 2) throw Error('sin datos'); }), /sin datos/);
  assert.equal((await pendientes()).length, 1);
  assert.equal(await subirPendientes(async () => {}), 1);
  assert.equal((await pendientes()).length, 0);
});
