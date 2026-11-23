#include "VecinosEspNow.h"

#include <WiFi.h>
#include <esp_now.h>
#include <esp_random.h>
#include <esp_wifi.h>

VecinosEspNow *VecinosEspNow::instancia_ = nullptr;

namespace {
const uint8_t DIFUSION[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
constexpr size_t COLA_BALIZAS = 8;  // sobra: caben 5 vecinos por segundo
constexpr size_t COLA_TRAMAS = 16;  // fragmentos por atender; el emisor manda de uno en uno
constexpr size_t COLA_PEDIDOS = 4;
constexpr uint32_t ESPERA_AVISO_PEDIDO_MS = 100;  // si la radio no avisa de un PEDIDO, se da por pasado
}  // namespace

VecinosEspNow::~VecinosEspNow() {
  if (iniciado_) esp_now_deinit();
  if (cola_) vQueueDelete(cola_);
  if (colaTramas_) vQueueDelete(colaTramas_);
  if (colaPedidos_) vQueueDelete(colaPedidos_);
  if (instancia_ == this) instancia_ = nullptr;
}

void VecinosEspNow::anotar(const String &texto) const {
  if (log_) log_(texto);
}

String VecinosEspNow::macTexto(const uint8_t mac[6]) {
  char s[18];
  snprintf(s, sizeof(s), "%02X:%02X:%02X:%02X:%02X:%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return s;
}

// El AP oculto por el que sale ESP-NOW (usarInterfazAp): sin clientes que valgan (clave al azar, uno como mucho) y
// en el canal de ESP-NOW. Con la STA conectada, el driver lo deja en el canal del router.
void VecinosEspNow::crearApOculto() {
  uint8_t mac[6];
  WiFi.macAddress(mac);
  char ssid[20], clave[20];
  snprintf(ssid, sizeof(ssid), "subepe-%02x%02x%02x", mac[3], mac[4], mac[5]);
  for (int i = 0; i < 16; i++) clave[i] = 'a' + esp_random() % 26;
  clave[16] = '\0';
  WiFi.softAP(ssid, clave, canal_, 1 /* oculto */, 1 /* clientes */);
}

// Las tramas de ESP-NOW salen y se oyen en largo alcance (LR) por su interfaz; la STA, en b/g/n de siempre
void VecinosEspNow::ponerProtocolos() {
  if (!interfazAp_) return;
  esp_wifi_set_protocol(WIFI_IF_STA, WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N);
  esp_wifi_set_protocol(WIFI_IF_AP, WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_LR);
}

void VecinosEspNow::fijarCanal() {
  if (interfazAp_) {
    if (WiFi.status() != WL_CONNECTED) crearApOculto();  // conectada, el canal lo pone el router
    return;
  }
  // Con el WiFi sin asociar, el canal de la radio solo se cambia en modo promiscuo
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(canal_, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);
}

void VecinosEspNow::cambiarCanal(uint8_t canal) {
  const uint8_t nuevo = canal ? canal : canalBase_;
  if (nuevo == canal_) return;
  canal_ = nuevo;
  if (!iniciado_) return;
  if (WiFi.status() != WL_CONNECTED) fijarCanal();
  anotar("ESP-NOW: al canal " + String(canal_) + (canal ? " (el del WiFi)" : " (espnow_canal, sin WiFi)"));
}

void VecinosEspNow::iniciar(uint8_t canalWifi) {
  if (iniciado_) return;
  canal_ = canalWifi ? canalWifi : 1;
  canalBase_ = canal_;
  if (!cola_) cola_ = xQueueCreate(COLA_BALIZAS, sizeof(Recibida));
  if (!colaTramas_) colaTramas_ = xQueueCreate(COLA_TRAMAS, sizeof(TramaCruda));
  if (!colaPedidos_) colaPedidos_ = xQueueCreate(COLA_PEDIDOS, sizeof(PedidoRecibido));
  if (!cola_ || !colaTramas_ || !colaPedidos_) {
    anotar("ESP-NOW: ERROR, no hay memoria para la cola de balizas");
    return;
  }

  WiFi.mode(interfazAp_ ? WIFI_AP_STA : WIFI_STA);
  ifz_ = static_cast<uint8_t>(interfazAp_ ? WIFI_IF_AP : WIFI_IF_STA);
  ponerProtocolos();
  // Sin ahorro de energía del WiFi: con el STA conectado a un AP, el modo por defecto (modem sleep) apaga la radio
  // entre balizas del AP y se pierde casi todo lo de ESP-NOW (medido: 1 baliza de cada ~8 entre dos placas con WiFi;
  // el vecino aparecía y se perdía cada 10 s). El framework guarda el valor y lo vuelve a aplicar en cada arranque
  // del STA. Cuesta corriente, pero el validador va enchufado. Ojo con BLE (M7): WiFi+Bluetooth a la vez exigen
  // modem sleep.
  WiFi.setSleep(false);
  fijarCanal();
  if (esp_now_init() != ESP_OK) {
    anotar("ESP-NOW: ERROR al iniciar");
    return;
  }
  esp_now_peer_info_t par = {};
  memcpy(par.peer_addr, DIFUSION, 6);
  par.channel = 0;  // el canal actual de la radio
  par.ifidx = static_cast<wifi_interface_t>(ifz_);
  par.encrypt = false;
  if (esp_now_add_peer(&par) != ESP_OK) {
    anotar("ESP-NOW: ERROR al registrar la difusión");
    esp_now_deinit();
    return;
  }
  instancia_ = this;
  esp_now_register_recv_cb(&VecinosEspNow::alRecibir);
  esp_now_register_send_cb(reinterpret_cast<esp_now_send_cb_t>(&VecinosEspNow::alEnviar));
  idMensaje_ = esp_random();
  iniciado_ = true;
  programarSiguiente(millis(), true);
  uint8_t propiaMac[6];
  if (interfazAp_) WiFi.softAPmacAddress(propiaMac);
  else WiFi.macAddress(propiaMac);
  anotar("ESP-NOW: iniciado en el canal " + String(canal_) + ", MAC " + macTexto(propiaMac) +
         (interfazAp_ ? " (interfaz AP)" : ""));
}

// Corre en la tarea del WiFi: solo filtra y encola; la tabla y el reensamblador los toca atender()
void VecinosEspNow::alRecibir(const uint8_t *mac, const uint8_t *datos, int largo) {
  VecinosEspNow *yo = instancia_;
  if (!yo || !yo->cola_ || largo < 6 || datos[0] != 'E' || datos[1] != 'N') return;
  if (datos[2] == sp::BALIZA) {
    if (largo < (int)sp::TAM_BALIZA) return;
    Recibida r;
    memcpy(r.mac, mac, 6);
    memcpy(r.datos, datos, sp::TAM_BALIZA);
    yo->recibidas_++;
    if (xQueueSend(yo->cola_, &r, 0) != pdTRUE) yo->perdidasCola_++;
  } else if (datos[2] == sp::FRAGMENTO && largo <= (int)sp::TAM_TRAMA_MAXIMO) {
    TramaCruda t;
    memcpy(t.mac, mac, 6);
    t.largo = largo;
    memcpy(t.datos, datos, largo);
    if (xQueueSend(yo->colaTramas_, &t, 0) != pdTRUE) yo->tramasPerdidas_++;
  } else if (datos[2] == sp::PEDIDO && yo->colaPedidos_) {
    PedidoRecibido p;
    if (!sp::leerPedido(datos, largo, p.pedido)) return;
    memcpy(p.mac, mac, 6);
    if (xQueueSend(yo->colaPedidos_, &p, 0) != pdTRUE) yo->pedidosPerdidos_++;
  }
}

// Corre en la tarea del WiFi: la radio dice si el último fragmento dirigido llegó. Las
// balizas (a difusión) también pasan por aquí y se ignoran.
void VecinosEspNow::alEnviar(const uint8_t *mac, int estado) {
  VecinosEspNow *yo = instancia_;
  if (!yo || memcmp(mac, DIFUSION, 6) == 0) return;
  yo->avisoLlego_ = estado == ESP_NOW_SEND_SUCCESS;
  yo->avisoListo_ = true;
}

void VecinosEspNow::programarSiguiente(uint32_t ahoraMs, bool primera) {
  if (primera) {
    // La primera sale enseguida (con un poco de azar): así dos placas que arrancan a la
    // vez se ven en menos de 3 s, sin esperar un periodo entero
    proximaMs_ = ahoraMs + esp_random() % (VARIACION_MS + 1);
    return;
  }
  const int32_t variacion = (int32_t)(esp_random() % (2 * VARIACION_MS + 1)) - (int32_t)VARIACION_MS;
  proximaMs_ = ahoraMs + (uint32_t)((int32_t)BALIZA_MS + variacion);
}

void VecinosEspNow::enviarBaliza() {
  uint8_t trama[sp::TAM_TRAMA_MAXIMO];
  const size_t largo = sp::armarBaliza(propia_, trama);
  ponerTasa(tasaBalizas_);
  if (esp_now_send(DIFUSION, trama, largo) == ESP_OK) {
    enviadas_++;
  } else {
    erroresEnvio_++;
  }
}

void VecinosEspNow::atender() {
  if (!iniciado_ || pausado_) return;
  const uint32_t ahora = millis();

  Recibida r;
  while (xQueueReceive(cola_, &r, 0) == pdTRUE) {
    sp::Baliza b;
    if (!sp::leerBaliza(r.datos, sp::TAM_BALIZA, b)) continue;
    if (b.validador == propia_.validador) continue;  // un bus con nuestro mismo número: no es un vecino
    const bool nuevo = tabla_.actualizar(r.mac, b, ahora) == TablaVecinos::Cambio::NUEVO;
    if (nuevo) {
      anotar("ESP-NOW: vecino " + String(b.validador) + " (" + macTexto(r.mac) + ") detectado; pendientes " +
             String(b.pendientes) + ", ajenos " + String(b.ajenos) + ", listas v" + String(b.versionListas) +
             ". Vecinos: " + String(tabla_.cantidad()));
    }
  }

  Vecino perdidos[TablaVecinos::MAXIMO];
  const size_t n = tabla_.caducar(ahora, perdidos);
  for (size_t i = 0; i < n; i++) {
    if (esp_now_is_peer_exist(perdidos[i].mac)) esp_now_del_peer(perdidos[i].mac);  // el de un PEDIDO se quedó registrado
    anotar("ESP-NOW: vecino " + String(perdidos[i].baliza.validador) + " (" + macTexto(perdidos[i].mac) +
           ") perdido: sin balizas desde hace " + String(TablaVecinos::CADUCIDAD_MS / 1000) + " s. Vecinos: " +
           String(tabla_.cantidad()));
  }

  atenderRecepcion(ahora);
  if (silencio_) return;  // el WiFi se está conectando: nada sale (lo pendiente sigue después)
  atenderEnvio(ahora);

  // Mientras se transfiere un mensaje la radio se deja para los fragmentos; la baliza espera
  if ((int32_t)(ahora - proximaMs_) >= 0 && !emisor_.ocupado()) {
    enviarBaliza();
    programarSiguiente(ahora, false);
  }
}

void VecinosEspNow::atenderRecepcion(uint32_t ahoraMs) {
  TramaCruda t;
  while (xQueueReceive(colaTramas_, &t, 0) == pdTRUE) {
    if (reens_.recibir(t.datos, t.largo, ahoraMs) != sp::Reensamblador::Resultado::COMPLETO) continue;
    if (cuenta_ >= MENSAJES_EN_ESPERA) {  // nadie los está recogiendo
      sinSitio_++;
      continue;
    }
    MensajeRecibido &m = espera_[(cabeza_ + cuenta_) % MENSAJES_EN_ESPERA];
    memcpy(m.mac, t.mac, 6);
    m.largo = reens_.largo();
    memcpy(m.datos, reens_.mensaje(), m.largo);
    cuenta_++;
  }
  reens_.revisar(ahoraMs);
}

void VecinosEspNow::atenderEnvio(uint32_t ahoraMs) {
  if (esperandoAvisoPedido_) {
    // El aviso de entrega de un PEDIDO no es de ningún fragmento: se descarta, y mientras llega no sale un fragmento
    // (se confundiría con el suyo)
    if (avisoListo_) {
      avisoListo_ = false;
      esperandoAvisoPedido_ = false;
    } else if ((uint32_t)(ahoraMs - pedidoMs_) >= ESPERA_AVISO_PEDIDO_MS) {
      esperandoAvisoPedido_ = false;
    }
    return;
  }
  if (avisoListo_) {
    avisoListo_ = false;
    if (esperandoAviso_) {
      esperandoAviso_ = false;
      emisor_.resultado(avisoLlego_, ahoraMs);
    }
  }
  emisor_.revisar(ahoraMs);
  if (esperandoAviso_ && !emisor_.ocupado()) esperandoAviso_ = false;

  const EmisorFragmentos::Fin fin = emisor_.tomarFin();
  if (fin == EmisorFragmentos::Fin::ENTREGADO) {
    enviados_++;
    soltarDestino();
  } else if (fin == EmisorFragmentos::Fin::ABORTADO) {
    abortados_++;
    anotar("ESP-NOW: mensaje a " + macTexto(destino_) + " ABORTADO: un fragmento no llegó tras " +
           String(emisor_.maxReintentos()) + " reintentos");
    soltarDestino();
  }

  if (emisor_.ocupado() && !esperandoAviso_) {
    uint8_t trama[sp::TAM_TRAMA_MAXIMO];
    const size_t largo = emisor_.siguienteTrama(trama, ahoraMs);
    if (largo == 0) return;
    ponerTasa(emisor_.intento() == 0 ? tasaDatos_ : tasaReintentos_);
    if (esp_now_send(destino_, trama, largo) == ESP_OK) {
      esperandoAviso_ = true;
      avisoListo_ = false;
    } else {
      erroresEnvio_++;
      emisor_.resultado(false, ahoraMs);  // la radio no la aceptó: cuenta como un fallo
    }
  }
}

// La radio solo transmite con la tasa que se le configuró; se cambia justo antes de cada envío. Es
// seguro porque los fragmentos salen de uno en uno (se espera el aviso de entrega) y las balizas
// solo salen cuando no hay un mensaje en curso, así que nunca hay dos tramas en vuelo a la vez.
void VecinosEspNow::ponerTasa(Tasa t) {
  if ((int)t == tasaPuesta_) return;
  const wifi_phy_rate_t tasa = t == Tasa::LR250 ? WIFI_PHY_RATE_LORA_250K : t == Tasa::LR500 ? WIFI_PHY_RATE_LORA_500K : WIFI_PHY_RATE_1M_L;
  if (esp_wifi_config_espnow_rate(static_cast<wifi_interface_t>(ifz_), tasa) == ESP_OK) {
    tasaPuesta_ = (int)t;
  } else {
    erroresEnvio_++;
  }
}

void VecinosEspNow::fijarTasas(Tasa datos, Tasa reintentos, Tasa balizas) {
  tasaDatos_ = datos;
  tasaReintentos_ = reintentos;
  tasaBalizas_ = balizas;
  // Aceptar largo alcance al recibir, aunque se transmita en ESTANDAR: así un vecino que use LR se oye
  if (interfazAp_) ponerProtocolos();
  else esp_wifi_set_protocol(WIFI_IF_STA, WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_LR);
  // La próxima trama vuelve a configurar la radio, salvo que todo siga en ESTANDAR (no se toca la del driver)
  tasaPuesta_ = (datos == Tasa::ESTANDAR && reintentos == Tasa::ESTANDAR && balizas == Tasa::ESTANDAR) ? 0 : -1;
}

void VecinosEspNow::soltarDestino() {
  if (hayDestino_) esp_now_del_peer(destino_);
  hayDestino_ = false;
}

VecinosEspNow::Envio VecinosEspNow::enviarPedido(const uint8_t mac[6], const sp::Pedido &pedido) {
  if (!activo()) return Envio::PAUSADO;
  if (silencio_ || emisor_.ocupado() || esperandoAvisoPedido_) return Envio::OCUPADO;
  if (!tabla_.esVecino(mac)) return Envio::NO_ES_VECINO;
  if (!esp_now_is_peer_exist(mac)) {
    esp_now_peer_info_t par = {};
    memcpy(par.peer_addr, mac, 6);
    par.channel = 0;
    par.ifidx = static_cast<wifi_interface_t>(ifz_);
    par.encrypt = false;
    if (esp_now_add_peer(&par) != ESP_OK) return Envio::ERROR_RADIO;
  }
  uint8_t trama[sp::TAM_TRAMA_MAXIMO];
  const size_t largo = sp::armarPedido(pedido, trama);
  ponerTasa(tasaDatos_);
  avisoListo_ = false;
  if (esp_now_send(mac, trama, largo) != ESP_OK) {
    erroresEnvio_++;
    return Envio::ERROR_RADIO;
  }
  esperandoAvisoPedido_ = true;
  pedidoMs_ = millis();
  return Envio::ACEPTADO;
}

bool VecinosEspNow::recibirPedido(uint8_t macOrigen[6], sp::Pedido &pedido) {
  PedidoRecibido p;
  if (!colaPedidos_ || xQueueReceive(colaPedidos_, &p, 0) != pdTRUE) return false;
  memcpy(macOrigen, p.mac, 6);
  pedido = p.pedido;
  return true;
}

VecinosEspNow::Envio VecinosEspNow::enviar(const uint8_t mac[6], const uint8_t *mensaje, size_t largo) {
  if (!activo()) return Envio::PAUSADO;
  if (mensaje == nullptr || largo == 0 || largo > MAX_MENSAJE) return Envio::MENSAJE_INVALIDO;
  if (emisor_.ocupado() || esperandoAvisoPedido_) return Envio::OCUPADO;
  if (!tabla_.esVecino(mac)) return Envio::NO_ES_VECINO;

  esp_now_peer_info_t par = {};
  memcpy(par.peer_addr, mac, 6);
  par.channel = 0;  // el canal actual de la radio
  par.ifidx = static_cast<wifi_interface_t>(ifz_);
  par.encrypt = false;
  const bool existia = esp_now_is_peer_exist(mac);
  if (!existia && esp_now_add_peer(&par) != ESP_OK) return Envio::ERROR_RADIO;

  if (tabla_.reservarEnvio(mac) != TablaVecinos::Reserva::CONCEDIDA) {
    if (!existia) esp_now_del_peer(mac);
    return Envio::LIMITE_ALCANZADO;
  }
  memcpy(destino_, mac, 6);
  hayDestino_ = !existia;  // solo se retira el par que se registró para esta transferencia
  emisor_.iniciar(mensaje, largo, propia_.validador, ++idMensaje_);
  esperandoAviso_ = false;
  return Envio::ACEPTADO;
}

size_t VecinosEspNow::recibir(uint8_t *buf, size_t max, uint8_t *macOrigen) {
  if (cuenta_ == 0) return 0;
  MensajeRecibido &m = espera_[cabeza_];
  const size_t largo = m.largo;
  if (macOrigen) memcpy(macOrigen, m.mac, 6);
  const size_t copia = largo < max ? largo : max;
  if (buf && copia) memcpy(buf, m.datos, copia);
  cabeza_ = (cabeza_ + 1) % MENSAJES_EN_ESPERA;
  cuenta_--;
  if (largo > max) return 0;  // no cabía: se pierde, pero no se queda atascado
  recibidosOk_++;
  return largo;
}

void VecinosEspNow::pausar(bool si) {
  if (!iniciado_ || si == pausado_) return;
  pausado_ = si;
  if (si) {
    // Pausado no se oye a nadie: la tabla dejaría de ser verdad
    tabla_.vaciar();
    emisor_.cancelar();
    esperandoAviso_ = false;
    avisoListo_ = false;
    soltarDestino();
    reens_ = sp::Reensamblador();
    cuenta_ = 0;
    Recibida descartada;
    while (xQueueReceive(cola_, &descartada, 0) == pdTRUE) {
    }
    TramaCruda descartadaT;
    while (xQueueReceive(colaTramas_, &descartadaT, 0) == pdTRUE) {
    }
    PedidoRecibido descartadoP;
    while (colaPedidos_ && xQueueReceive(colaPedidos_, &descartadoP, 0) == pdTRUE) {
    }
    esperandoAvisoPedido_ = false;
    anotar("ESP-NOW: en pausa (el WiFi usa otro canal)");
  } else {
    if (interfazAp_) {
      // El portal deja la radio en solo AP y luego en solo STA: la interfaz AP por la que iba ESP-NOW desaparece y,
      // medido, rehacer solo el AP oculto no bastaba (el vecino no volvía a oírse). Se reinicia ESP-NOW entero sobre
      // el AP nuevo: protocolos, velocidad, callbacks y el par de difusión
      esp_now_deinit();
      WiFi.mode(WIFI_AP_STA);
      ponerProtocolos();
      crearApOculto();
      bool ok = esp_now_init() == ESP_OK;
      if (ok) {
        esp_now_register_recv_cb(&VecinosEspNow::alRecibir);
        esp_now_register_send_cb(reinterpret_cast<esp_now_send_cb_t>(&VecinosEspNow::alEnviar));
        esp_now_peer_info_t par = {};
        memcpy(par.peer_addr, DIFUSION, 6);
        par.ifidx = static_cast<wifi_interface_t>(ifz_);
        ok = esp_now_add_peer(&par) == ESP_OK;
      }
      if (tasaPuesta_ != 0) tasaPuesta_ = -1;  // la próxima trama vuelve a fijar la velocidad
      hayDestino_ = false;  // los pares unicast se borraron con el deinit
      if (!ok) anotar("ESP-NOW: ERROR al reiniciarlo tras el portal");
    }
    programarSiguiente(millis(), true);
    anotar("ESP-NOW: reanudado");
  }
}
