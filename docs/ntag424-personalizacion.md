# Personalización de la NTAG 424 DNA (M1-02)

> **Estado: borrador parcial.** Este documento solo fija lo que ya está cerrado
> técnicamente y verificado contra los documentos de NXP. Las secciones marcadas
> **PENDIENTE** dependen de decisiones abiertas del módulo M1 y no se deben
> implementar hasta que se cierren (ver [§12](#12-decisiones-pendientes)).
> Nada de lo que hay aquí se ha probado todavía con una tarjeta NTAG 424 real.

Objetivo: que `personalizar_424.py` (M1-06), el controlador Python (M1-04), el
simulador (M1-03) y el firmware (M1-07) se puedan implementar sin reinterpretar
las hojas de datos. El área lógica de 72 bytes y la URL SUN vienen del contrato
C3 (`docs/plan-modulos-y-tareas.html`, sección 7); este documento no los cambia.

## 1. Referencias

Documentos públicos de NXP, descargables en nxp.com. **No se copian al
repositorio** (sus condiciones de redistribución no están comprobadas); se citan
por revisión, sección y página.

| Abreviatura | Documento | Revisión |
|---|---|---|
| **DS** | NT4H2421Gx, *NTAG 424 DNA – Secure NFC T4T compliant IC*, hoja de datos | Rev. 3.0, 31 de enero de 2019 |
| **AN12196** | *NTAG 424 DNA and NTAG 424 DNA TagTamper features and hints* | Rev. 2.0, 4 de marzo de 2025 |
| **AN10922** | *Symmetric key diversifications* | Rev. 2.2, 2 de julio de 2019 |

Las citas tienen la forma «DS §10.6.1, p. 62» (número de página impreso en el
pie del documento). Si NXP publica otra revisión, hay que volver a comprobar
cada cita.

**Verificación.** Todos los ejemplos de NXP marcados con ✔ se recalcularon con
`cryptography==50.0.2` (la versión fijada para M1) y coinciden byte a byte. Los
vectores se repiten en [§11](#11-vectores-verificados) para usarlos en pruebas.

## 2. Estructura de la tarjeta de fábrica

| Archivo | FileNo | ISO FileID | Tamaño | Uso en SubePe | Referencia |
|---|---|---|---|---|---|
| CC | 01h | E103h | 32 B | Capability Container (sin cambios) | DS §8.2.3, Tabla 5, p. 10 |
| NDEF | 02h | E104h | 256 B | URL SUN de C3 | DS §8.2.3, Tabla 5, p. 10 |
| Propietario | 03h | E105h | 128 B | Área lógica de 72 B de C3 (offsets 0–71) | DS §8.2.3, Tabla 5, p. 10 |

Permisos y modos de comunicación de fábrica:

| Archivo | Read | Write | ReadWrite | Change | CommMode | Referencia |
|---|---|---|---|---|---|---|
| 01h | E | 0 | 0 | 0 | Plain | DS Tabla 8, p. 12; Tabla 13, p. 14 |
| 02h | E | E | E | 0 | Plain | DS Tabla 8, p. 12; Tabla 13, p. 14 |
| 03h | 2 | 3 | 3 | 0 | Full | DS Tabla 8, p. 12; Tabla 13, p. 14 |

`E` = acceso libre; `0`–`4` = número de clave. Las cinco claves de aplicación
(K0–K4) vienen a cero (AN12196 §5.6, Tabla 14, p. 27, paso 2). La hoja de datos
advierte que Write y ReadWrite del archivo 02 **deben cambiarse** al personalizar
(DS Tabla 8, nota 1, p. 12). SDM está desactivado de fábrica y solo se puede
activar en el archivo 02 (DS §8.2.3.1, p. 10).

ReadData se autoriza con la clave Read **o** la ReadWrite, y WriteData con la
Write **o** la ReadWrite (DS Tabla 9, p. 12).

## 3. Formato de los comandos

Los comandos nativos van envueltos en APDU ISO 7816-4 con `CLA = 90`:

```
90 <INS> 00 00 <Lc> <CmdHeader || CmdData [|| MACt]> 00
```

La respuesta termina en `91 <código>`; `91 00` es éxito y `91 AF` pide la
siguiente trama (DS §10.3, Tabla 23, p. 45). ISOSelectFile usa `CLA = 00` y
responde `90 00` (DS Tabla 24, p. 45).

| Código | Nombre | Significado |
|---|---|---|
| 9100 | OPERATION_OK | Éxito |
| 911C | ILLEGAL_COMMAND_CODE | Comando no soportado |
| 911E | INTEGRITY_ERROR | MAC, CRC o relleno incorrectos |
| 9140 | NO_SUCH_KEY | Número de clave inválido |
| 917E | LENGTH_ERROR | Longitud del comando inválida |
| 919D | PERMISSION_DENIED | La configuración o el estado no lo permiten |
| 919E | PARAMETER_ERROR | Parámetro inválido |
| 91AD | AUTHENTICATION_DELAY | Autenticación bloqueada temporalmente (ver §10) |
| 91AE | AUTHENTICATION_ERROR | Falta la autenticación que exige el comando |
| 91AF | ADDITIONAL_FRAME | Se espera otra trama |
| 91BE | BOUNDARY_ERROR | Lectura/escritura fuera del archivo |
| 91CA | COMMAND_ABORTED | Comando anterior incompleto |
| 91F0 | FILE_NOT_FOUND | El archivo no existe |

Fuente: DS §10.3, Tabla 23, p. 45. Con un error en modo MAC o Full se pierde la
autenticación y la respuesta llega **sin MAC** (DS §9.1.10, p. 28).

Comandos que usa la personalización (DS §10.2, Tabla 22, p. 44):

| Comando | APDU | CommMode | Referencia de detalle |
|---|---|---|---|
| ISOSelectFile | `00 A4 …` | Plain | DS §10.9.1, p. 77 |
| AuthenticateEV2First (parte 1 / parte 2) | `90 71 …` / `90 AF …` | — | DS §10.4.1, p. 46 |
| ChangeKey | `90 C4 …` | Full | DS §10.6.1, p. 62 |
| ChangeFileSettings | `90 5F …` | Full | DS §10.7.1, p. 65 |
| GetFileSettings | `90 F5 …` | MAC | DS §10.7.2, p. 69 |
| GetFileCounters | `90 F6 …` | Full | DS §10.7.3, p. 71 |
| ReadData | `90 AD …` | el del archivo | DS §10.8.1, p. 73 |
| WriteData | `90 8D …` | el del archivo | DS §10.8.2, p. 75 |

### 3.1 Selección de la aplicación

```
00 A4 04 00 07 D2 76 00 00 85 01 01 00
```

`D2760000850101` selecciona directamente la aplicación NDEF (DS §10.9.1, p. 77;
ejemplo en AN12196 §5.3, p. 23).

## 4. AuthenticateEV2First

Autenticación mutua AES en tres pasos (DS §9.1.5, p. 24; §10.4.1, p. 46):

1. `90 71 00 00 02 <KeyNo> 00 00` → `E(Kx, RndB) || 91 AF`.
   `LenCap = 00` (DS Tabla 25, p. 46: «This value should be set to 00h»).
2. El lector descifra RndB, genera RndA (16 B aleatorios) y rota RndB un byte a
   la izquierda (RndB′).
3. `90 AF 00 00 20 E(Kx, RndA || RndB′) 00` → `E(Kx, TI || RndA′ || PDcap2 || PCDcap2) || 91 00`.
4. El lector descifra, comprueba que RndA′ es RndA rotado y guarda TI (4 B).

El cifrado de la autenticación es AES-CBC con IV de ceros y **sin relleno**
(DS §9.1.4, p. 24).

### 4.1 Claves de sesión

Según DS §9.1.7, p. 26 (NIST SP 800-108 en modo contador, PRF = AES-CMAC con Kx):

```
contexto = RndA[15..14] || (RndA[13..8] ⊕ RndB[15..10]) || RndB[9..0] || RndA[7..0]
SV1 = A5 5A 00 01 00 80 || contexto         (32 B)
SV2 = 5A A5 00 01 00 80 || contexto         (32 B)
SesAuthENCKey = CMAC(Kx, SV1)
SesAuthMACKey = CMAC(Kx, SV2)
```

La notación `RndA[15..14]` numera los bytes de mayor a menor: en un arreglo
`ra[0..15]` (`ra[0]` es el primer byte transmitido) equivale a `ra[0:2]`, y
`RndA[13..8] ⊕ RndB[15..10]` a `ra[2:8] ⊕ rb[0:6]`.

✔ AN12196 §5.6, Tabla 14, pp. 27–28 (K0 = 00…00):

| Paso | Valor |
|---|---|
| E(K0, RndB) | `A04C124213C186F22399D33AC2A30215` |
| RndB | `B9E2FC789B64BF237CCCAA20EC7E6E48` |
| RndA | `13C5DB8A5930439FC3DEF9A4C675360F` |
| E(K0, RndA ∥ RndB′) | `35C3E05A752E0144BAC0DE51C1F22C56B34408A23D8AEA266CAB947EA8E0118D` |
| TI | `9D00C4DF` |
| SesAuthENCKey | `1309C877509E5A215007FF0ED19CA564` |
| SesAuthMACKey | `4C6626F5E72EA694202139295C7A7FC7` |

## 5. Mensajería segura (AES)

| Concepto | Regla | Referencia |
|---|---|---|
| TI | 4 B que entrega la parte 2 de la autenticación; se trata como arreglo de bytes | DS §9.1.1, p. 23 |
| CmdCtr | 16 bits, se pone a 0000h tras AuthenticateEV2First, va **LSB primero** en los cálculos, y sube en 1 entre comando y respuesta | DS §9.1.2, p. 23 |
| MAC | AES-CMAC con IV de ceros; se transmiten 8 B: los bytes de **índice impar** (1, 3, …, 15) del CMAC (la hoja de datos los llama «even-numbered» contando desde 1) | DS §9.1.3, p. 23 |
| MAC del comando | `CMAC(SesAuthMACKey, Cmd ‖ CmdCtr ‖ TI ‖ CmdHeader ‖ CmdData)` | DS §9.1.9, p. 27 |
| MAC de la respuesta | `CMAC(SesAuthMACKey, RC ‖ CmdCtr+1 ‖ TI ‖ RespData)` | DS §9.1.9, p. 27 |
| IV de cifrado | `E_ECB(SesAuthENCKey, A5 5A ‖ TI ‖ CmdCtr ‖ 00×8)` para comandos; `5A A5 …` para respuestas | DS §9.1.4, p. 24 |
| Cifrado (Full) | AES-CBC, relleno ISO/IEC 9797-1 método 2 (`80 00…`, siempre, también si ya es múltiplo de 16) | DS §9.1.4, p. 24 |
| Full | se cifra CmdData y después se aplica MAC sobre el cifrado | DS §9.1.10, p. 28 |

✔ Ejemplo de comando Full: ChangeFileSettings de AN12196 §5.9, Tabla 18, pp. 31–32
(claves de sesión de §4.1, CmdCtr = 1):

| Paso | Valor |
|---|---|
| IVc | `3E27082AB2ACC1EF55C57547934E9962` |
| E(SesAuthENCKey, CmdData ∥ 80) | `61B6D97903566E84C3AE5274467E89EA` |
| MACt del comando | `D799B7C1A0EF7A04` |
| C-APDU | `905F0000190261B6D97903566E84C3AE5274467E89EAD799B7C1A0EF7A0400` |
| MACt de la respuesta (`RC = 00`, CmdCtr = 2) | `57BFF87B1241E93D` |

## 6. ChangeKey

DS §10.6.1, p. 62, Tabla 63. Exige estar autenticado con **K0** y va en
CommMode.Full. `KeyNo` va en claro como CmdHeader; KeyData va cifrado:

| Clave que se cambia | KeyData (antes de cifrar) | Longitud |
|---|---|---|
| K0 | `NewKey ‖ KeyVer` | 17 B |
| K1–K4 | `(NewKey ⊕ OldKey) ‖ KeyVer ‖ CRC32NK` | 21 B |

**CRC32NK.** La hoja de datos lo define como el CRC de IEEE 802.3 (FCS) sobre
NewKey (DS Tabla 63, nota 1). En la práctica son los 4 bytes de
`~zlib.crc32(NewKey) & 0xFFFFFFFF` escritos **LSB primero**; así se reproduce el
ejemplo oficial (abajo). No usar `zlib.crc32` sin complementar.

✔ AN12196 §5.16.1, Tabla 25, pp. 37–38 (cambiar K2 autenticado con K0, OldKey = 0):

| Paso | Valor |
|---|---|
| SesAuthENCKey / SesAuthMACKey | `4CF3CB41A22583A61E89B158D252FC53` / `5529860B2FC5FB6154B7F28361D30BF9` |
| TI, CmdCtr | `7614281A`, 2 |
| NewKey, KeyVer | `F3847D627727ED3BC9C4CC050489B966`, `01` |
| CRC32NK | `789DFADC` |
| IVe | `307EDE1814707F30CFE603DD6CA62353` |
| E(KeyData ∥ relleno) | `2CF362B7BF4311FF3BE1DAA295E8C68DE09050560D19B9E16C2393AE9CD1FAC7` |
| CMAC / MACt | `EA5D2E0CBFE24C0BCBCD501D21060EE6` / `5D0CE20BCD1D06E6` |
| C-APDU | `90C4000029022CF362B7BF4311FF3BE1DAA295E8C68DE09050560D19B9E16C2393AE9CD1FAC75D0CE20BCD1D06E600` |

**PENDIENTE (decisión A, M1-05):** qué valores toman K0–K4 (diversificación
AN10922 con la entrada M de 16 bytes, aún sin conformidad de firmware), qué
`KeyVer` se graba y en qué orden se cambian. K3 no se diversifica (AN12196
§3.4.2, p. 10). No se documenta todavía el caso «cambiar la clave con la que se
está autenticado» (AN12196 §5.16.2, p. 38).

## 7. Escrituras interrumpidas (*tearing*)

La hoja de datos dice:

- «blocks up to 128 bytes are either completely written of not changed at all»
  (DS §8.6, p. 19);
- las escrituras de **una sola trama** de hasta 128 B con WriteData o
  ISOUpdateBinary están protegidas (DS §8.2.3.1, p. 10);
- con encadenamiento ISO 14443-4 cada trama es atómica, pero una cadena
  interrumpida puede dejar el archivo inconsistente; se recomienda WriteData de
  una sola trama (DS §10.8.2, p. 75);
- si falla el MAC de un WriteData, la tarjeta no programa nada más y aborta
  (DS §10.8.2, p. 75).

**Conclusión para SubePe:**

1. Cada `escribir_cabecera` (24 B) y cada `escribir_registro` (20 B) se manda
   en **un solo WriteData sin encadenar**. En CommMode.Full, un registro ocupa
   7 B de cabecera (FileNo, Offset, Length) + 32 B cifrados (20 B + relleno) +
   8 B de MAC = 47 B de datos; la cabecera, también 7 + 32 + 8 = 47 B.
2. **Se mantiene el doble registro A/B** del contrato C3: la atomicidad por
   trama no sustituye al mecanismo de dos registros, que además protege frente
   a una tarjeta retirada entre dos comandos.
3. **PENDIENTE (coordinación con firmware):** confirmar que el tamaño de trama
   del ACR122U y del PN532 admite esos 47 B + 6 B de APDU sin encadenar.

## 8. Archivo 02: NDEF con SUN

### 8.1 Contenido (cerrado por el contrato C3)

URL: `https://mi.subepe.app/t?p=<32 hex>&m=<16 hex>`, hexadecimal ASCII en
mayúsculas (SDMOptions con modo ASCII, §8.3).

Archivo de 76 bytes (los 32 + 16 caracteres de `p` y `m` los rellena la tarjeta
en cada lectura; al grabar se ponen a `'0'`):

| Offset | Bytes | Contenido |
|---|---|---|
| 0x00 | `00 4A` | NLEN = 74 (registro NDEF) |
| 0x02 | `D1 01 46 55` | Registro corto, MB/ME, tipo `U` (URI), carga de 70 B |
| 0x06 | `04` | Prefijo URI `https://` (NFC Forum URI RTD) |
| 0x07 | `mi.subepe.app/t?p=` | 18 B ASCII |
| **0x19** | `p` | 32 B: PICCData cifrado → **PICCDataOffset = 0x19** |
| 0x39 | `&m=` | 3 B ASCII |
| **0x3C** | `m` | 16 B: SDMMAC → **SDMMACOffset = 0x3C** |

Comprobado: la carga útil mide 1 + 18 + 32 + 3 + 16 = 70 B (0x46) y el registro
4 + 70 = 74 B (0x4A). Estructura análoga al ejemplo de AN12196 §5.7, Tabla 15,
p. 28.

### 8.2 PICCData y SDMReadCtr

- PICCData = `AES-CBC(K3, IV = 0)` de `PICCDataTag ‖ UID(7) ‖ SDMReadCtr(3, LSB primero) ‖ relleno aleatorio de 5 B`,
  con `PICCDataTag = C7` (UID y contador en el espejo, UID de 7 B)
  (DS §9.3.4, Tabla 21, p. 37).
  ✔ AN12196 §3.4.2.2, p. 11: `EF963FF7828658A599F3041510671E88` →
  `C704DE5F1EACC0403D0000DA5CF60941` (UID `04DE5F1EACC040`, contador 61).
- SDMReadCtr (24 bits) **vuelve a 000000h cuando se activa SDM** con
  ChangeFileSettings y solo sube con lecturas **sin autenticar** del archivo 02
  (DS §9.3.1, p. 35).
- Claves de sesión SUN (DS §9.3.9.1, p. 41):
  `SV2 = 3C C3 00 01 00 80 ‖ UID ‖ SDMReadCtr` y
  `SesSDMFileReadMACKey = CMAC(K4, SV2)`.
  ✔ AN12196 §3.3, p. 9: `KSesSDMFileReadMAC = 3A3E8110E05311F7A3FCF0D969BF2B48`.
- `SDMMAC = MACt(SesSDMFileReadMACKey, DynamicFileData[SDMMACInputOffset … SDMMACOffset − 1])`,
  con el mismo truncado que la mensajería segura (DS §9.3.8.1, p. 40).

**PENDIENTE (decisión D):** sobre qué datos se calcula el SDMMAC
(SDMMACInputOffset). AN12196 da los dos casos: entrada vacía
(SDMMACInputOffset = SDMMACOffset, §3.4.4.2.1, p. 14) y entrada no vacía
(§3.4.4.2.2, p. 17).

### 8.3 ChangeFileSettings del archivo 02

DS §10.7.1, p. 65; ejemplo de NXP en AN12196 §5.9, Tabla 18, p. 31
(`CmdData = 40 00E0 C1 F121 200000 430000 430000`).

Campos ya fijados por C3 o por la hoja de datos:

| Campo | Valor | Motivo |
|---|---|---|
| FileOption | `40` | SDM activado, CommMode.Plain (AN12196 Tabla 18, paso 7) |
| SDMOptions | `C1` | UID y SDMReadCtr en el espejo, sin límite, sin SDMENCFileData, ASCII (AN12196 Tabla 18, paso 7) |
| SDMMetaRead | K3 | C3: PICCData con K3 común a todas las tarjetas |
| SDMFileRead | K4 | C3: SDMMAC con K4 diversificada |
| PICCDataOffset | `19 00 00` | §8.1 (3 B, LSB primero) |
| SDMMACOffset | `3C 00 00` | §8.1 |

Codificación de AccessRights en el cable, según la descomposición de AN12196
Tabla 18: primer byte = `ReadWrite ‖ Change`, segundo = `Read ‖ Write` (un
nibble cada uno). Así, `00 E0` es ReadWrite = 0, Change = 0, Read = E, Write = 0.

**PENDIENTE:**

- AccessRights del archivo 02 (Write, ReadWrite): se propone `00 E0`, pero
  está ligada a la decisión de custodia de claves (A).
- SDMCtrRet (quién puede leer el contador con GetFileCounters): decisión C'
  (`contador_sdm`).
- SDMMACInputOffset: decisión D.

Hasta que se cierren, el comando completo **no está definido**.

## 9. Archivo 03: área lógica de C3

C3 fija el uso: offsets 0–71 del archivo 03, lectura con K1, escritura con K2,
cambio con K0, CommMode.Full.

**PENDIENTE (decisión B):** el valor exacto de AccessRights (en particular
ReadWrite) y el ChangeFileSettings correspondiente. De fábrica el archivo ya
está en CommMode.Full pero con Read = 2 y Write = 3 (DS Tabla 8, p. 12;
Tabla 13, p. 14), así que **hay que cambiarlo**.

## 10. Contador de autenticaciones fallidas

**PENDIENTE (decisión F):** valores del límite y del retardo, y si se
configuran al personalizar. Referencia: AN12196 §6.4, p. 43, y el código
`91AD AUTHENTICATION_DELAY` (DS Tabla 23, p. 45).

## 11. Vectores verificados

Para las pruebas de M1-03 y M1-04 y para el firmware. Todos son de NXP; ninguno
es propio de SubePe.

| Uso | Fuente | Entradas | Resultado esperado |
|---|---|---|---|
| AuthenticateEV2First con K0 | AN12196 Tabla 14 | ver §4.1 | SesAuthENCKey `1309C877509E5A215007FF0ED19CA564`, SesAuthMACKey `4C6626F5E72EA694202139295C7A7FC7` |
| Comando Full (ChangeFileSettings) | AN12196 Tabla 18 | ver §5 | C-APDU `905F0000190261B6D97903566E84C3AE5274467E89EAD799B7C1A0EF7A0400`, MACt de la respuesta `57BFF87B1241E93D` |
| ChangeKey de K2 | AN12196 Tabla 25 | ver §6 | CRC32NK `789DFADC`; C-APDU en §6 |
| Descifrado de PICCData | AN12196 §3.4.2.2 | K3 = 00…00 | `C704DE5F1EACC0403D0000DA5CF60941` |
| Clave de sesión SUN | AN12196 §3.3 | K4 = 00…00 | `3A3E8110E05311F7A3FCF0D969BF2B48` |

## 12. Decisiones pendientes

| Sección | Decisión | Bloquea |
|---|---|---|
| §6 | A: custodia y valores de K0–K4, KeyVer, orden de ChangeKey; M de AN10922 sin conformidad de firmware (M1-05, M2-04) | M1-06 |
| §7 | Tamaño de trama del ACR122U/PN532 (firmware) | M1-04, M1-07 |
| §8.2, §8.3 | D: SDMMACInput | M1-03 (SUN), M1-08 |
| §8.3 | C': SDMCtrRet y uso de `contador_sdm` | M1-06, M1-08 |
| §9 | B: AccessRights del archivo 03 | M1-03 (errores de acceso), M1-04, M1-06 |
| §10 | F: contador de autenticaciones fallidas | M1-06 |
| §13 | Orden completo de la personalización | M1-06 |

## 13. Orden de la personalización

**PENDIENTE.** Esqueleto, a completar cuando se cierren las decisiones de §12:

1. ISOSelectFile de la aplicación (§3.1).
2. AuthenticateEV2First con K0 de fábrica (§4).
3. Escribir el NDEF del archivo 02 (§8.1) y el área inicial del archivo 03.
4. ChangeFileSettings del archivo 03 (§9) y del archivo 02 (§8.3). Activar SDM
   pone SDMReadCtr a cero (§8.2).
5. ChangeKey de K1–K4 y, por último, K0 (§6).
6. Verificación en una sesión nueva: GetFileSettings de 02 y 03, autenticación
   con las claves nuevas y lectura del área.

Que el paso 5 vaya al final, y el orden dentro de él, depende de la decisión A.
