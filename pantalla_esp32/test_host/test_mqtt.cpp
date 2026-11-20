#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <algorithm>

#include <CanalMqtt.h>
#include <EnlaceFalso.h>

uint32_t relojFalso = 100;

void verificar(EnlaceFalso &enlace, const char *nombre, uint8_t codigo) {
  Client cliente;
  enlace.ponerCliente(&cliente);
  enlace.ponerEstado(EstadoEnlace::Listo);
  CanalMqtt canal(enlace, "mqtt.example.test", 103, "clave-de-prueba");
  assert(strcmp(canal.nombre(), nombre) == 0 && canal.codigoEnlace() == codigo);
  canal.atender();
  assert(canal.listo());
  assert(enlace.ultimoPuerto() == 8883);
  assert(strcmp(enlace.ultimoHost(), "mqtt.example.test") == 0);
  assert(PubSubClient::buffer == 2200 && PubSubClient::keepalive == 60);
  assert(PubSubClient::usuario == "103" && PubSubClient::clave == "clave-de-prueba");
  assert(PubSubClient::will == "sp/v/103/conexion" && PubSubClient::limpio);
  assert(std::find(PubSubClient::suscripciones.begin(), PubSubClient::suscripciones.end(), "sp/v/103/baja") != PubSubClient::suscripciones.end());
  assert(std::find(PubSubClient::suscripciones.begin(), PubSubClient::suscripciones.end(), "sp/todos/urgente") != PubSubClient::suscripciones.end());

  const uint8_t paquete[] = {1, 0, 3, 0};
  assert(canal.enviar(paquete, sizeof(paquete)));
  assert(PubSubClient::temas.back() == "sp/v/103/sube" && PubSubClient::publicado.size() == 4);
  PubSubClient::ultimo->inyectar("sp/v/103/baja", paquete, sizeof(paquete));
  uint8_t recibido[4] = {};
  assert(canal.recibir(recibido, 2) == 0);  // no pierde el paquete si no cabe
  assert(canal.recibir(recibido, sizeof(recibido)) == 4);
  assert(memcmp(recibido, paquete, sizeof(paquete)) == 0);
  PubSubClient::ultimo->inyectar("sp/otro", paquete, sizeof(paquete));
  assert(canal.recibir(recibido, sizeof(recibido)) == 0);
  PubSubClient::ultimo->inyectar("sp/todos/urgente", paquete, sizeof(paquete));
  PubSubClient::ultimo->inyectar("sp/v/103/baja", paquete, sizeof(paquete));
  PubSubClient::ultimo->inyectar("sp/v/103/baja", paquete, sizeof(paquete));
  assert(canal.descartados() == 1);
  assert(canal.recibir(recibido, sizeof(recibido)) == 4);
  assert(canal.recibir(recibido, sizeof(recibido)) == 4);
  canal.suspender(true);
  assert(!canal.listo() && !canal.enviar(paquete, sizeof(paquete)));
  assert(PubSubClient::temas.back() == "sp/v/103/conexion" && PubSubClient::publicado == "0");
}

int main() {
  EnlaceFalso wifi("wifi", 1);
  verificar(wifi, "mqtt-wifi", 1);
  EnlaceFalso sinCliente("wifi", 1);
  sinCliente.ponerEstado(EstadoEnlace::Listo);
  CanalMqtt reintento(sinCliente, "mqtt.example.test", 103, "clave-de-prueba");
  relojFalso = 100; reintento.atender();
  relojFalso = 500; reintento.atender();
  assert(sinCliente.aperturas() == 1);
  relojFalso = 1100; reintento.atender();
  assert(sinCliente.aperturas() == 2);  // respeta la espera tras fallo TLS
  puts("M15 MQTT: EnlaceFalso WiFi, TLS, tópicos, will, cola y reconexión OK");
}
