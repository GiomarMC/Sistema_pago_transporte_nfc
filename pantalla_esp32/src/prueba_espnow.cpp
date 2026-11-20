/*
 * Prueba de ESP-NOW entre buses: balizas y tabla de vecinos (M8-01) y transferencia de
 * mensajes de 2 KB (M8-02).
 *
 * Cargar en DOS placas:  pio run -e prueba_espnow -t upload
 * Ver los mensajes:      pio device monitor
 *
 * No usa WiFi, pantalla, microSD ni PN532: solo la ESP32. El LED azul de la placa (GPIO2)
 * se enciende mientras haya al menos un vecino.
 *
 * M8-01: cada placa escribe "vecino ... detectado" al ver a la otra (menos de 3 s) y
 * "perdido" a los 10 s sin balizas.
 *
 * M8-02: cuando se ven, la placa de número de validador MÁS BAJO envía 20 mensajes de
 * 2048 B (el máximo de un paquete C1) a la otra, con un contenido que depende de la
 * ronda y del número de mensaje. La otra comprueba cada byte. Se repite en 10 rondas, y
 * entre una y otra se reinicia el encuentro (el límite es de 20 mensajes por encuentro).
 *   Emisor:   "[emisor] RONDA n: 20 aceptados, 20 entregados, 0 abortados, ..."
 *   Receptor: "[receptor] RONDA n: recibidos 20/20, íntegros 20, corruptos 0, ..."
 * Esperado: 10 rondas con los 20 mensajes íntegros cada una y 0 abortados. Al final el
 * emisor escribe "PRUEBA M8-02 COMPLETA".
 *
 * El número de validador de prueba sale de la MAC, así que cada placa tiene el suyo.
 */

#include <Arduino.h>
#include <VecinosEspNow.h>
#include <WiFi.h>

constexpr uint8_t PIN_LED = 2;
constexpr size_t LARGO_MENSAJE = 2048;
constexpr uint32_t MENSAJES_POR_RONDA = 20;
constexpr uint32_t RONDAS = 10;
constexpr uint32_t ESPERA_VECINO_MS = 2000;   // el vecino debe llevar este tiempo en la tabla
constexpr uint32_t PAUSA_RONDA_MS = 3000;     // para que el receptor escriba su resumen
constexpr uint32_t REINICIO_ENCUENTRO_MS = 800;
constexpr uint32_t SILENCIO_RECEPTOR_MS = 4000;

void registro(const String &texto) { Serial.printf("[%8lu ms] %s\n", (unsigned long)millis(), texto.c_str()); }

VecinosEspNow vecinos(registro);
sp::Baliza propia;

// --- Contenido de prueba -----------------------------------------------------------------

void armarMensaje(uint8_t *m, uint32_t ronda, uint32_t indice) {
  memcpy(m, &ronda, 4);
  memcpy(m + 4, &indice, 4);
  uint32_t x = ronda * 2654435761u + indice * 40503u + 1;
  for (size_t j = 8; j < LARGO_MENSAJE; j++) {
    x = x * 1664525u + 1013904223u;
    m[j] = x >> 24;
  }
}

static uint8_t bufferMensaje[LARGO_MENSAJE], bufferEsperado[LARGO_MENSAJE];

// --- Emisor ------------------------------------------------------------------------------

enum class Fase : uint8_t { ESPERA_VECINO, RECEPTOR, ENVIANDO, PAUSA, REINICIO, FIN };
Fase fase = Fase::ESPERA_VECINO;
uint32_t vecinoDesde = 0, faseDesde = 0;
uint32_t ronda = 0, siguiente = 0, inicioRondaMs = 0;
uint32_t entregadosAntes = 0, abortadosAntes = 0, reintentosAntes = 0;
uint32_t totalEntregados = 0, totalAbortados = 0, totalRondasOk = 0;
uint8_t macVecino[6];

void iniciarRonda() {
  ronda++;
  siguiente = 0;
  inicioRondaMs = millis();
  entregadosAntes = vecinos.mensajesEnviados();
  abortadosAntes = vecinos.mensajesAbortados();
  reintentosAntes = vecinos.fragmentosReintentados();
  memcpy(macVecino, vecinos.vecino(0).mac, 6);
  registro("[emisor] RONDA " + String(ronda) + " de " + String(RONDAS) + ": envío " + String(MENSAJES_POR_RONDA) +
           " mensajes de " + String(LARGO_MENSAJE) + " B al validador " + String(vecinos.vecino(0).baliza.validador));
  fase = Fase::ENVIANDO;
}

void cerrarRonda(bool error) {
  const uint32_t entregados = vecinos.mensajesEnviados() - entregadosAntes;
  const uint32_t abortados = vecinos.mensajesAbortados() - abortadosAntes;
  const uint32_t reintentos = vecinos.fragmentosReintentados() - reintentosAntes;
  const uint32_t ms = millis() - inicioRondaMs;
  totalEntregados += entregados;
  totalAbortados += abortados;
  if (entregados == MENSAJES_POR_RONDA && abortados == 0 && !error) totalRondasOk++;
  registro("[emisor] RONDA " + String(ronda) + ": " + String(siguiente) + " aceptados, " + String(entregados) +
           " entregados, " + String(abortados) + " abortados, " + String(reintentos) + " fragmentos reintentados, " +
           String(ms) + " ms" + (error ? "  (RONDA INTERRUMPIDA)" : ""));
  fase = Fase::PAUSA;
  faseDesde = millis();
}

void atenderEmisor() {
  switch (fase) {
    case Fase::ENVIANDO: {
      if (siguiente < MENSAJES_POR_RONDA) {
        armarMensaje(bufferMensaje, ronda, siguiente);
        const auto r = vecinos.enviar(macVecino, bufferMensaje, LARGO_MENSAJE);
        if (r == VecinosEspNow::Envio::ACEPTADO) {
          siguiente++;
        } else if (r != VecinosEspNow::Envio::OCUPADO) {
          registro("[emisor] ERROR: enviar() devolvió " + String((int)r) + " en el mensaje " + String(siguiente));
          while (vecinos.enviando()) {  // deja terminar el que va en curso
            vecinos.atender();
            delay(2);
          }
          cerrarRonda(true);
        }
      } else if (!vecinos.enviando()) {
        cerrarRonda(false);
      }
      break;
    }
    case Fase::PAUSA:
      if (millis() - faseDesde < PAUSA_RONDA_MS) break;
      if (ronda >= RONDAS) {
        registro("PRUEBA M8-02 COMPLETA: " + String(RONDAS) + " rondas, " + String(RONDAS * MENSAJES_POR_RONDA) +
                 " mensajes: " + String(totalEntregados) + " entregados, " + String(totalAbortados) +
                 " abortados, rondas perfectas " + String(totalRondasOk) + "/" + String(RONDAS));
        fase = Fase::FIN;
        break;
      }
      vecinos.pausar(true);  // vacía la tabla: el próximo encuentro empieza de cero
      fase = Fase::REINICIO;
      faseDesde = millis();
      break;
    case Fase::REINICIO:
      if (millis() - faseDesde < REINICIO_ENCUENTRO_MS) break;
      vecinos.pausar(false);
      vecinoDesde = 0;
      fase = Fase::ESPERA_VECINO;
      break;
    default:
      break;
  }
}

// --- Receptor ----------------------------------------------------------------------------

uint32_t rondaRecibida = 0, recibidos = 0, integros = 0, corruptos = 0, duplicados = 0, vistos = 0;
uint32_t ultimoMensajeMs = 0;
uint32_t acumRecibidos = 0, acumIntegros = 0;

void resumenReceptor() {
  String faltan;
  for (uint32_t i = 0; i < MENSAJES_POR_RONDA; i++)
    if (!(vistos & (1u << i))) faltan += (faltan.length() ? "," : "") + String(i);
  acumRecibidos += recibidos;
  acumIntegros += integros;
  registro("[receptor] RONDA " + String(rondaRecibida) + ": recibidos " + String(recibidos) + "/" + String(MENSAJES_POR_RONDA) +
           ", íntegros " + String(integros) + ", corruptos " + String(corruptos) + ", duplicados " + String(duplicados) +
           (faltan.length() ? ", FALTAN " + faltan : "") + " | acumulado: " + String(acumIntegros) + " íntegros de " +
           String(acumRecibidos) + " recibidos");
  recibidos = integros = corruptos = duplicados = vistos = 0;
}

void atenderReceptor() {
  uint8_t mac[6];
  const size_t n = vecinos.recibir(bufferMensaje, sizeof(bufferMensaje), mac);
  if (n > 0) {
    uint32_t r = 0, i = 0;
    if (n >= 8) {
      memcpy(&r, bufferMensaje, 4);
      memcpy(&i, bufferMensaje + 4, 4);
    }
    if (recibidos > 0 && r != rondaRecibida) resumenReceptor();  // empezó otra ronda sin quedar en silencio
    rondaRecibida = r;
    ultimoMensajeMs = millis();
    recibidos++;
    if (i < MENSAJES_POR_RONDA && (vistos & (1u << i))) duplicados++;
    if (i < MENSAJES_POR_RONDA) vistos |= 1u << i;
    armarMensaje(bufferEsperado, r, i);
    if (n == LARGO_MENSAJE && memcmp(bufferMensaje, bufferEsperado, LARGO_MENSAJE) == 0) {
      integros++;
    } else {
      corruptos++;
      registro("[receptor] mensaje " + String(i) + " de la ronda " + String(r) + " CORRUPTO (" + String(n) + " B)");
    }
  }
  if (recibidos > 0 && millis() - ultimoMensajeMs >= SILENCIO_RECEPTOR_MS) resumenReceptor();
}

// --- Principal ---------------------------------------------------------------------------

uint32_t ultimaTabla = 0;

void imprimirTabla() {
  Serial.printf("[%8lu ms] --- %u vecino(s) | balizas enviadas %lu, recibidas %lu | mensajes: enviados %lu, abortados %lu, "
                "recibidos %lu, descartados %lu\n",
                (unsigned long)millis(), (unsigned)vecinos.cantidad(), (unsigned long)vecinos.balizasEnviadas(),
                (unsigned long)vecinos.balizasRecibidas(), (unsigned long)vecinos.mensajesEnviados(),
                (unsigned long)vecinos.mensajesAbortados(), (unsigned long)vecinos.mensajesRecibidos(),
                (unsigned long)vecinos.mensajesDescartados());
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED, OUTPUT);
  WiFi.mode(WIFI_STA);
  uint8_t mac[6];
  WiFi.macAddress(mac);

  propia.validador = 1 + ((mac[4] << 8) | mac[5]) % 899;  // 1-899
  propia.flags = sp::BALIZA_ACEPTA_AJENOS;
  vecinos.publicar(propia);
  registro("Prueba ESP-NOW: validador de prueba " + String(propia.validador));
  vecinos.iniciar(1);
}

void loop() {
  vecinos.atender();
  digitalWrite(PIN_LED, vecinos.cantidad() > 0 ? HIGH : LOW);

  if (fase == Fase::ESPERA_VECINO || fase == Fase::RECEPTOR) {
    if (vecinos.cantidad() == 0) {
      vecinoDesde = 0;
    } else {
      if (vecinoDesde == 0) vecinoDesde = millis();
      // La de número más bajo envía; la otra recibe
      if (fase == Fase::ESPERA_VECINO && millis() - vecinoDesde >= ESPERA_VECINO_MS) {
        if (propia.validador < vecinos.vecino(0).baliza.validador) {
          iniciarRonda();
        } else {
          registro("[receptor] soy el validador " + String(propia.validador) + ": espero los mensajes del " +
                   String(vecinos.vecino(0).baliza.validador));
          fase = Fase::RECEPTOR;
        }
      }
    }
  }
  atenderEmisor();
  atenderReceptor();

  if (millis() - ultimaTabla >= 10000) {
    ultimaTabla = millis();
    imprimirTabla();
  }
  delay(2);
}
