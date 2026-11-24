// Datos locales del validador en la microSD (equivalente a validador_local.py).
// Archivos de texto, para poder leerlos en un ordenador si hace falta:
//   /config.txt      validador=103 / clave_maestra=<64 hex> wifi_ssid, wifi_clave, servidor, token
//   /eventos.bin     viajes, incidencias y fraudes pendientes de subir (binario, ColaEventos); /estado.bin lleva
//   /estado.bin      siguiente_id, hasta_acusado, versiones y secuencia (escritura atómica, EstadoPersistente).
//                    Los /eventos.txt y /estado.txt de versiones anteriores se migran solos (quedan como *.migrado)
//   /ultima_op.txt   "id_tarjeta operación" por línea (detecta copias restauradas)
//   /lista_negra.txt un id de tarjeta por línea         } los escribe la sincronización
//   /tarifas.bin     cuerpo del paquete TARIFAS de C1   } con el servidor; si no existen
//   /recargas.bin    registros (cuenta, seq, monto)     } se usan valores por defecto
//                    (binarios, a prueba de cortes de energía: ver Recargas y TarifasGuardadas;
//                    los /tarifas.txt y /recargas.txt de versiones anteriores se migran solos)
//   /redes.txt       "red<TAB>contraseña" por línea: redes WiFi (portal de configuración)
#pragma once
#include <Arduino.h>
#include <map>
#include <memory>
#include <vector>

#include <AplicadorListas.h>
#include <ArchivosSD.h>
#include <ColaEventos.h>
#include <ConsumoDatos.h>
#include <EstadoPersistente.h>
#include <PaquetesGuardados.h>
#include <RedWifi.h>
#include <Recargas.h>
#include <TarifasGuardadas.h>

#include "cobros_dudosos.h"

struct Tarifa {
  String nombre;
  int32_t precio;  // céntimos
};

class Almacen {
 public:
  Almacen()
      : estado_(archivos_), cola_(archivos_, estado_, avisarCola), recargas_(archivos_), tarifasBin_(archivos_),
        ajenos_(archivos_), firmados_(archivos_), consumo_(archivos_), listaNegraBin_(archivos_),
        aplicador_(estado_, listaNegraBin_, recargas_, tarifasBin_, firmados_) {}
  // Monta la microSD y carga lo necesario en memoria. Si la tarjeta no contesta (pasa tras un
  // reinicio brusco en pleno acceso) reintenta INTENTOS_MONTAJE veces, reiniciando el bus SPI.
  bool iniciar();
  // Desde loop(): si sigue sin montar, lo intenta cada REINTENTO_MONTAJE_MS. Devuelve true
  // solo en la llamada en que consigue montarla (para que la pantalla se actualice).
  bool reintentarMontaje();
  static constexpr uint8_t INTENTOS_MONTAJE = 4;
  static constexpr uint32_t ESPERA_MONTAJE_MS = 300;
  static constexpr uint32_t REINTENTO_MONTAJE_MS = 3000;
  bool listo() const { return listo_; }

  // Configuración
  bool configurado() const { return validador_ > 0 && tieneClave_; }
  uint16_t validador() const { return validador_; }
  const uint8_t *claveMaestra() const { return clave_; }
  bool guardarConfig(const String &pares);  // "clave=valor;clave=valor"
  String config(const String &clave) const;

  // Redes WiFi guardadas (/redes.txt), en orden de preferencia
  static constexpr size_t MAX_REDES = 5;
  const std::vector<RedWifi> &redes() const { return redes_; }
  bool guardarRed(const String &ssid, const String &clave);  // la pone la primera
  bool olvidarRed(const String &ssid);

  // Consultas para cobrar
  bool bloqueada(uint32_t idTarjeta);
  bool tarifa(uint8_t codigo, Tarifa &t) const;
  uint32_t ultimaOperacion(uint32_t idTarjeta) const;
  bool existeViaje(uint32_t idTarjeta, uint16_t operacion);
  // Cobros escritos en la tarjeta sin poder comprobarlos (#237), guardados en /cobros_dudosos.txt
  void anotarCobroDudoso(const CobroDudoso &c);
  // La tarjeta se volvió a leer: qué pasó con su cobro dudoso, si tenía uno (ver CobrosDudosos::resolver)
  CobrosDudosos::Resultado resolverCobroDudoso(uint32_t idTarjeta, uint16_t operacion, uint16_t validadorTarjeta,
                                               CobroDudoso &c);
  // Recargas correlativas de la cuenta posteriores a `ultima`: suma y hasta qué seq
  void recargasPendientes(uint32_t idCuenta, uint32_t ultima, int32_t &total, uint32_t &hasta) const;

  // Registro (cada evento se guarda en la microSD antes de dar el resultado)
  bool registrarViaje(uint32_t fecha, uint32_t idTarjeta, const String &uid, int32_t monto,
                      int32_t saldoFinal, uint16_t operacion, uint32_t contador,
                      uint32_t recargaHasta);
  bool registrarIncidencia(uint32_t fecha, uint32_t idTarjeta, const String &uid,
                           const String &detalle, bool fraude);
  // Paquetes de otros buses por reenviar y LISTAS firmados por compartir (M3-05; los usan M8 y M4)
  PaquetesAjenos &ajenos() { return ajenos_; }
  PaquetesFirmados &firmados() { return firmados_; }
  // Consumo de datos por canal y día (M3-06): de ahí salen bytesTxHoy y bytesRxHoy del HOLA
  ConsumoDatos &consumo() { return consumo_; }
  // Los eventos y el estado en binario, para el sincronizador v2 (AlmacenSyncBinario)
  ColaEventos &cola() { return cola_; }
  EstadoPersistente &estado() { return estado_; }
  uint32_t pendientes() const { return cola_.pendientes(); }
  // Modo sync=v2 (config.txt): la lista negra es la binaria (la que llena el sincronizador v2 con AplicadorListas); en
  // v1 sigue siendo el mapa en RAM que llena la sincronización JSON. Se decide al montar la tarjeta.
  bool modoV2() const { return v2_; }
  AplicadorListas &aplicador() { return aplicador_; }
  ListaNegra &listaNegraBinaria() { return listaNegraBin_; }
  PaquetesAjenos &paquetesAjenos() { return ajenos_; }

  // Sincronización con el servidor
  // Eventos aún no subidos (como array JSON), hasta `max`; sus ids en `ids`
  String eventosPendientes(size_t max, std::vector<uint32_t> &ids);
  void marcarSubidos(uint32_t hastaId);  // confirmados por el servidor hasta este id
  // Trabajo de fondo que cuesta tiempo en la microSD (compactar la cola de eventos): desde loop(), con el lector
  // libre, un paso por llamada. true si hizo algo.
  bool mantenimiento();
  // Textos con el formato de cada archivo (ver arriba); se guardan y se cargan
  bool guardarListas(const String &listaNegra, const String &tarifas, const String &recargas);
  size_t tamListaNegra() const { return listaNegra_.size(); }
  size_t numRecargas() const { return recargas_.cantidad(); }
  // Se llama con cada evento nuevo ya guardado en la microSD (con su id): main.cpp anota los de hora estimada
  void alGuardarEvento(void (*aviso)(const sp::Evento &)) { alGuardar_ = aviso; }

 private:
  void (*alGuardar_)(const sp::Evento &) = nullptr;
  bool montar();       // un intento de montar la tarjeta
  void cargarDatos();  // lee config, estado y listas de la tarjeta ya montada
  bool guardarEvento(sp::Evento &e);
  static void avisarCola(const char *texto);
  void prepararMigracion();
  void migrarEventosDeTexto();
  void guardarUltimasOperaciones();
  void cargarConfig();
  void cargarListas();
  void migrarListasDeTexto(bool hayRecargas, bool hayTarifas);
  bool guardarTarifasDeTexto(const String &texto);  // /tarifas.txt y /recargas.txt de versiones anteriores -> .bin
  void cargarRedes();
  bool guardarRedes();

  uint8_t fallosCompactar_ = 0;  // la compactación se deja tras 3 fallos seguidos, hasta el siguiente acuse
  bool listo_ = false;
  uint32_t ultimoIntentoMontaje_ = 0;
  uint16_t validador_ = 0;
  uint8_t clave_[32] = {0};
  bool tieneClave_ = false;
  std::map<String, String> config_;
  std::map<uint32_t, uint32_t> ultimaOp_;
  CobrosDudosos dudosos_;
  void guardarCobrosDudosos();
  std::map<uint32_t, bool> listaNegra_;
  ArchivosSD archivos_;  // la microSD ya montada, vista como SistemaArchivos (raíz)
  EstadoPersistente estado_;
  ColaEventos cola_;
  Recargas recargas_;
  TarifasGuardadas tarifasBin_;
  PaquetesAjenos ajenos_;
  PaquetesFirmados firmados_;
  ConsumoDatos consumo_;
  ListaNegra listaNegraBin_;   // modo v2
  AplicadorListas aplicador_;  // modo v2
  bool v2_ = false;
  std::vector<RedWifi> redes_;
};
