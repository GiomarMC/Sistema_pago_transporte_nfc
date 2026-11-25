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

// Un TrabajoEnFondo que no hace nada hasta que el test lo dice: así se ve el canal "a mitad" de conectar
struct FondoFalso : TrabajoEnFondo {
  Trabajo trabajo = nullptr;
  void *arg = nullptr;
  bool rechazar = false;
  int lanzados = 0;
  bool lanzar(Trabajo t, void *a) override {
    if (rechazar || trabajo) return false;
    trabajo = t; arg = a; ++lanzados;
    return true;
  }
  void terminar() {
    Trabajo t = trabajo;
    trabajo = nullptr;
    t(arg);
  }
};

// #129: conectar (TLS + CONNECT) va en otra tarea y atender() vuelve enseguida
void conectar_en_fondo() {
  Client cliente;
  EnlaceFalso enlace;
  enlace.ponerCliente(&cliente);
  enlace.ponerEstado(EstadoEnlace::Listo);
  FondoFalso fondo;
  relojFalso = 1000;
  CanalMqtt canal(enlace, "mqtt.example.test", 104, "clave", &fondo);
  canal.atender();
  assert(fondo.lanzados == 1 && canal.conectando() && enlace.aperturas() == 0);  // se lanzó y aún no se hizo nada
  assert(enlace.prestado());  // el enlace no cierra el flujo por su cuenta mientras tanto
  const uint8_t p[] = {1, 2};
  assert(!canal.listo() && !canal.enviar(p, sizeof(p)));
  canal.atender();
  canal.atender();
  assert(fondo.lanzados == 1);  // mientras conecta no se lanza otro intento
  fondo.terminar();             // la otra tarea conecta
  assert(enlace.aperturas() == 1 && !canal.conectando());
  assert(!canal.listo());       // hasta que atender() lo recoge en loop()
  relojFalso = 4500;
  canal.atender();
  assert(canal.listo() && !enlace.prestado() && canal.ultimaConexionMs() == 3500);
  assert(canal.enviar(p, sizeof(p)) && PubSubClient::temas.back() == "sp/v/104/sube");
  canal.atender();
  assert(fondo.lanzados == 1);  // conectado: no se vuelve a conectar

  // suspender a mitad: espera a que termine la otra tarea y entonces desconecta (con el "0")
  EnlaceFalso enlace2;
  enlace2.ponerCliente(&cliente);
  enlace2.ponerEstado(EstadoEnlace::Listo);
  FondoFalso fondo2;
  CanalMqtt canal2(enlace2, "mqtt.example.test", 105, "clave", &fondo2);
  canal2.atender();
  assert(canal2.conectando());
  canal2.suspender(true);
  assert(enlace2.cierres() == 0);  // no se tocó el flujo que usa la otra tarea
  fondo2.terminar();
  canal2.atender();
  assert(!canal2.listo() && !enlace2.prestado() && enlace2.cierres() == 1);
  assert(PubSubClient::temas.back() == "sp/v/105/conexion" && PubSubClient::publicado == "0");
  canal2.suspender(false);
  canal2.atender();
  assert(fondo2.lanzados == 2);  // al reanudar vuelve a conectar

  // desconectar() con la otra tarea ya terminada y sin recoger: se recoge cerrando
  fondo2.terminar();
  canal2.desconectar();
  assert(!canal2.listo() && !canal2.conectando() && !enlace2.prestado());

  // falla en la otra tarea (sin cliente): se reintenta con la espera de siempre
  EnlaceFalso sinCliente;
  sinCliente.ponerEstado(EstadoEnlace::Listo);
  FondoFalso fondo3;
  relojFalso = 10000;
  CanalMqtt canal3(sinCliente, "mqtt.example.test", 106, "clave", &fondo3);
  canal3.atender();
  fondo3.terminar();
  relojFalso = 10200;
  canal3.atender();  // recoge el fallo: próximo intento en 1 s
  assert(!canal3.listo() && !sinCliente.prestado() && fondo3.lanzados == 1);
  relojFalso = 11000;
  canal3.atender();
  assert(fondo3.lanzados == 1);
  relojFalso = 11300;
  canal3.atender();
  assert(fondo3.lanzados == 2);
  fondo3.terminar();  // el destructor espera a la otra tarea: aquí no hay otra que la termine

  // sin tarea disponible (lanzar() falla): se conecta en el acto, como sin fondo
  EnlaceFalso enlace4;
  enlace4.ponerCliente(&cliente);
  enlace4.ponerEstado(EstadoEnlace::Listo);
  FondoFalso fondo4;
  fondo4.rechazar = true;
  CanalMqtt canal4(enlace4, "mqtt.example.test", 107, "clave", &fondo4);
  canal4.atender();
  assert(canal4.listo() && enlace4.prestamos() == 1 && !enlace4.prestado());
  puts("M15 MQTT: conectar en otra tarea (atender no espera, suspender y fallo a mitad, sin tarea en el acto) OK");
}

// #129: con la cola llena no se lee del broker (TCP lo frena) hasta que se saque algo, o pasados 5 s
void colaLlenaNoLee() {
  Client cliente;
  EnlaceFalso enlace("wifi", 1);
  enlace.ponerCliente(&cliente);
  enlace.ponerEstado(EstadoEnlace::Listo);
  relojFalso = 10000;
  CanalMqtt canal(enlace, "mqtt.example.test", 105, "clave");
  canal.atender();
  assert(canal.listo());
  const uint8_t paquete[] = {5, 0, 1, 0};
  uint8_t recibido[4];
  PubSubClient::ultimo->inyectar("sp/v/105/baja", paquete, sizeof(paquete));
  canal.atender();
  const int conUno = PubSubClient::vueltas;
  PubSubClient::ultimo->inyectar("sp/v/105/baja", paquete, sizeof(paquete));  // la cola, llena
  relojFalso += 100;
  canal.atender();
  relojFalso += 100;
  canal.atender();
  assert(PubSubClient::vueltas == conUno + 0 && canal.descartados() == 0);  // no lee: lo que venga espera en TCP
  assert(canal.recibir(recibido, sizeof(recibido)) == 4);
  canal.atender();
  assert(PubSubClient::vueltas == conUno + 1);  // con sitio, vuelve a leer
  // Si nadie la vacía, a los 5 s vuelve a leer (para no perder la conexión por el keepalive)
  PubSubClient::ultimo->inyectar("sp/v/105/baja", paquete, sizeof(paquete));
  canal.atender();
  relojFalso += 4900;
  canal.atender();
  assert(PubSubClient::vueltas == conUno + 1);
  relojFalso += 200;
  canal.atender();
  assert(PubSubClient::vueltas == conUno + 2 && canal.listo());
  puts("M15 MQTT: con la cola llena no lee del broker hasta que se saque algo o pasen 5 s OK");
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
  conectar_en_fondo();
  colaLlenaNoLee();
}
