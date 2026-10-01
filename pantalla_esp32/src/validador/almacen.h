// Datos locales del validador en la microSD (equivalente a validador_local.py).
// Archivos de texto, para poder leerlos en un ordenador si hace falta:
//   /config.txt      validador=103 / clave_maestra=<64 hex> (+ WiFi y servidor en la fase 4)
//   /eventos.txt     un JSON por línea: viajes, incidencias y fraudes pendientes de subir
//   /estado.txt      siguiente_id, ultimo_subido
//   /ultima_op.txt   "id_tarjeta operación" por línea (detecta copias restauradas)
//   /lista_negra.txt un id de tarjeta por línea         } los escribe la sincronización
//   /tarifas.txt     "código nombre precio" por línea   } (fase 4); si no existen se
//   /recargas.txt    "cuenta seq monto" por línea       } usan valores por defecto
#pragma once
#include <Arduino.h>
#include <map>
#include <vector>

struct Tarifa {
  String nombre;
  int32_t precio;  // céntimos
};

class Almacen {
 public:
  bool iniciar();  // monta la microSD y carga lo necesario en memoria
  bool listo() const { return listo_; }

  // Configuración
  bool configurado() const { return validador_ > 0 && tieneClave_; }
  uint16_t validador() const { return validador_; }
  const uint8_t *claveMaestra() const { return clave_; }
  bool guardarConfig(const String &pares);  // "clave=valor;clave=valor"
  String config(const String &clave) const;

  // Consultas para cobrar
  bool bloqueada(uint32_t idTarjeta) const;
  bool tarifa(uint8_t codigo, Tarifa &t) const;
  uint32_t ultimaOperacion(uint32_t idTarjeta) const;
  bool existeViaje(uint32_t idTarjeta, uint16_t operacion);
  // Recargas correlativas de la cuenta posteriores a `ultima`: suma y hasta qué seq
  void recargasPendientes(uint32_t idCuenta, uint32_t ultima, int32_t &total, uint32_t &hasta) const;

  // Registro (cada evento se guarda en la microSD antes de dar el resultado)
  bool registrarViaje(uint32_t fecha, uint32_t idTarjeta, const String &uid, int32_t monto,
                      int32_t saldoFinal, uint16_t operacion, uint32_t contador,
                      uint32_t recargaHasta);
  bool registrarIncidencia(uint32_t fecha, uint32_t idTarjeta, const String &uid,
                           const String &detalle, bool fraude);
  uint32_t pendientes() const { return siguienteId_ - 1 - ultimoSubido_; }

 private:
  bool agregarEvento(const String &json);
  void guardarEstado();
  void guardarUltimasOperaciones();
  void cargarConfig();
  void cargarListas();

  bool listo_ = false;
  uint16_t validador_ = 0;
  uint8_t clave_[32] = {0};
  bool tieneClave_ = false;
  std::map<String, String> config_;
  uint32_t siguienteId_ = 1;
  uint32_t ultimoSubido_ = 0;
  std::map<uint32_t, uint32_t> ultimaOp_;
  std::map<uint32_t, bool> listaNegra_;
  std::map<uint8_t, Tarifa> tarifas_;
  struct Recarga { uint32_t cuenta, seq; int32_t monto; };
  std::vector<Recarga> recargas_;
};
