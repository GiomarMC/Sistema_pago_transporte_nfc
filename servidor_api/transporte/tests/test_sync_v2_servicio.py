"""M9-03: procesar() de la sincronización v2."""

from datetime import timedelta
from itertools import count

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from transporte.models import (Alerta, CambioLista, Cuenta, EventoRecibido, Movimiento, Recarga,
                               Tarifa, Tarjeta, Validador)
from transporte import servicios
from transporte.sync_v2 import paquetes as p
from transporte.sync_v2 import servicio

CLAVE = ec.generate_private_key(ec.SECP256R1())
PEM = CLAVE.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
AHORA = 1793000000
_secuencias = count(1)


@override_settings(LISTAS_CLAVE_PRIVADA=PEM)
class Base(TestCase):
    def setUp(self):
        Usuario = get_user_model()
        self.v = {n: Validador.objects.create(numero=n, usuario=Usuario.objects.create(
            username=f"validador-{n}")) for n in (101, 102)}
        tarifa = Tarifa.objects.order_by("codigo").first()
        self.cuenta = Cuenta.objects.create(nombre="Ana", documento="45678912", tarifa=tarifa,
                                            saldo=1000)
        self.tarjeta = Tarjeta.objects.create(uid="04AABBCCDDEEFF", cuenta=self.cuenta,
                                              estado=Tarjeta.Estado.ACTIVA)

    # --- Paquetes de un validador ---

    def paquete(self, cuerpo, origen=101, clave_de=None, destino=p.SERVIDOR):
        cab = p.Cabecera(cuerpo.TIPO, origen=origen, destino=destino,
                         secuencia=next(_secuencias), creado=AHORA)
        clave = self.v[clave_de or origen].clave_hmac_bytes
        return p.empaquetar(cab, cuerpo, clave_hmac=clave)

    def viaje(self, id_evento, operacion=None, monto=-80, **extra):
        campos = dict(id_evento=id_evento, tipo=p.VIAJE, operacion=operacion or id_evento,
                      fecha=AHORA + id_evento, id_tarjeta=self.tarjeta.id,
                      uid=bytes.fromhex(self.tarjeta.uid), monto=monto, saldo_final=0,
                      contador=100 + id_evento)
        campos.update(extra)
        return p.Evento(**campos)

    def eventos(self, *eventos, **kw):
        return self.paquete(p.Eventos(eventos), **kw)

    def hola(self, version_listas=0, version_tarifas=0, enlace=p.WIFI, **kw):
        return self.paquete(p.Hola(version_listas, version_tarifas, ultimo_evento=0, pendientes=0,
                                   ajenos=0, enlace=enlace, senal=-30, bytes_tx_hoy=10,
                                   bytes_rx_hoy=20, version_firmware=20261114), **kw)

    # --- Respuestas del servidor ---

    def leer(self, respuestas):
        return [p.desempaquetar(r, lambda n: None, CLAVE.public_key()) for r in respuestas]

    def acuse(self, respuestas):
        (paquete,) = [x for x in self.leer(respuestas) if x.cabecera.tipo == p.ACUSE]
        return paquete.cuerpo.hasta_evento

    def listas(self, respuestas):
        return [x.cuerpo for x in self.leer(respuestas) if x.cabecera.tipo == p.LISTAS]

    def viajes(self):
        return Movimiento.objects.filter(tipo="viaje")

    def alertas(self, tipo):
        return list(Alerta.objects.filter(tipo=tipo).values_list("detalle", flat=True))


class Eventos(Base):
    def test_mismo_paquete_tres_veces_es_un_solo_viaje(self):
        datos = self.eventos(self.viaje(1))
        for _ in range(3):
            self.assertEqual(self.acuse(servicio.procesar(datos, "mqtt")), 1)
        self.assertEqual(self.viajes().count(), 1)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.saldo, 1000 - 80, "el monto negativo de C1 es un cobro")
        self.assertEqual(EventoRecibido.objects.get().via, "mqtt")

    def test_paquete_ajeno_subido_por_otro_validador(self):
        datos = self.eventos(self.viaje(1), origen=101)
        respuestas = servicio.procesar(datos, "espnow", portador=102)
        (acuse,) = self.leer(respuestas)
        self.assertEqual((acuse.cabecera.destino, acuse.cuerpo.hasta_evento), (101, 1),
                         "el ACUSE es para el origen, aunque lo suba otro")
        mov = self.viajes().get()
        self.assertEqual((mov.validador, mov.ref), (101, "V101-1"))
        recibido = EventoRecibido.objects.get()
        self.assertEqual((recibido.validador.numero, recibido.via), (101, "espnow"))
        self.assertEqual(self.v[102].eventos_recibidos.count(), 0)

    def test_firma_de_otro_validador_se_rechaza(self):
        datos = self.eventos(self.viaje(1), origen=101, clave_de=102)
        self.assertEqual(servicio.procesar(datos, "espnow", portador=102), [])
        self.assertEqual(self.viajes().count(), 0)
        (detalle,) = self.alertas("paquete inválido")
        self.assertIn("firma", detalle)
        self.assertIn("origen 101", detalle)
        self.assertIn("portador 102", detalle)

    def test_eventos_desordenados_mantienen_la_contiguidad(self):
        self.assertEqual(self.acuse(servicio.procesar(
            self.eventos(self.viaje(2), self.viaje(1), self.viaje(5)), "http")), 2)
        self.assertEqual(self.acuse(servicio.procesar(self.eventos(self.viaje(4)), "http")), 2)
        self.assertEqual(self.acuse(servicio.procesar(self.eventos(self.viaje(3)), "ble")), 5)
        self.v[101].refresh_from_db()
        self.assertEqual(self.v[101].ultimo_evento_contiguo, 5)
        self.assertEqual(self.viajes().count(), 5)

    def test_validador_que_pasa_de_v1_a_v2_cuenta_los_eventos_ya_subidos_por_v1(self):
        # Los viajes 1 a 3 y una incidencia (id 4) subieron por v1 (JSON): solo dejan movimiento y alerta
        for i in (1, 2, 3):
            servicios.procesar_evento(101, {
                "id": i, "tipo": "viaje", "fecha": "2026-11-21T14:03:09+00:00", "tarjeta_id": self.tarjeta.id,
                "uid": self.tarjeta.uid, "monto": 80, "saldo_final": 100, "operacion": i, "contador": 9,
                "recarga_hasta": None, "detalle": None})
        servicios.procesar_evento(101, {
            "id": 4, "tipo": "incidencia", "fecha": "2026-11-21T14:03:09+00:00", "tarjeta_id": self.tarjeta.id,
            "uid": self.tarjeta.uid, "monto": None, "saldo_final": None, "operacion": None, "contador": None,
            "recarga_hasta": None, "detalle": "prueba"})
        self.assertEqual(EventoRecibido.objects.filter(validador=self.v[101]).count(), 0)
        # El evento 5 llega ya por v2: se confirma hasta el 5, no hasta 0
        self.assertEqual(self.acuse(servicio.procesar(self.eventos(self.viaje(5)), "mqtt")), 5)
        self.v[101].refresh_from_db()
        self.assertEqual(self.v[101].ultimo_evento_contiguo, 5)
        # Un hueco de verdad sigue frenando: el 7 sin el 6
        self.assertEqual(self.acuse(servicio.procesar(self.eventos(self.viaje(7)), "mqtt")), 5)
        # Los ids de v1 de OTRO validador no cuentan
        servicios.procesar_evento(102, {
            "id": 1, "tipo": "viaje", "fecha": "2026-11-21T14:03:09+00:00", "tarjeta_id": self.tarjeta.id,
            "uid": self.tarjeta.uid, "monto": 80, "saldo_final": 100, "operacion": 1, "contador": 9,
            "recarga_hasta": None, "detalle": None})
        self.assertEqual(self.acuse(servicio.procesar(self.eventos(self.viaje(3), origen=102), "mqtt")), 1)

    def test_evento_corrupto_se_alerta_y_no_frena_la_contiguidad(self):
        raro = self.viaje(2, tipo=9)
        respuestas = servicio.procesar(self.eventos(self.viaje(1), raro, self.viaje(3)), "mqtt")
        self.assertEqual(self.acuse(respuestas), 3)
        self.assertEqual(self.viajes().count(), 2)
        (detalle,) = self.alertas("evento inválido")
        self.assertTrue(detalle.startswith("V101-2:"), detalle)
        # Reenviarlo no lo vuelve a intentar
        servicio.procesar(self.eventos(raro), "mqtt")
        self.assertEqual(len(self.alertas("evento inválido")), 1)

    def test_conversion_al_evento_v1(self):
        e = self.viaje(7, recarga_hasta=3, contador=p.SIN_CONTADOR, detalle="x")
        v1 = servicio.evento_a_v1(e)
        self.assertEqual(v1["tipo"], "viaje")
        self.assertEqual(v1["monto"], 80)
        self.assertEqual(v1["uid"], "04AABBCCDDEEFF")
        self.assertIsNone(v1["contador"])
        self.assertEqual(v1["recarga_hasta"], 3)
        self.assertEqual(v1["fecha"], "2026-10-26T07:33:27+00:00")
        self.assertIsNone(servicio.evento_a_v1(self.viaje(8))["recarga_hasta"])

    def test_viaje_entrega_recargas_y_fraude_bloquea(self):
        r = Recarga.objects.create(cuenta=self.cuenta, seq=1, monto=500, origen="web")
        servicio.procesar(self.eventos(self.viaje(1, recarga_hasta=1)), "mqtt")
        r.refresh_from_db()
        self.assertEqual(r.aplicada_por, 101)
        fraude = p.Evento(2, p.FRAUDE, 0, AHORA, self.tarjeta.id,
                          bytes.fromhex(self.tarjeta.uid), detalle="clon")
        servicio.procesar(self.eventos(fraude), "mqtt")
        self.tarjeta.refresh_from_db()
        self.assertEqual(self.tarjeta.estado, Tarjeta.Estado.BLOQUEADA)
        self.assertTrue(CambioLista.objects.filter(tipo=1, a=self.tarjeta.id,
                                                   urgente=True).exists())


class Hola(Base):
    def test_guarda_el_estado_del_validador(self):
        servicio.procesar(self.hola(version_listas=0, enlace=p.BLE), "ble")
        v = Validador.objects.get(numero=101)
        self.assertEqual(v.ultimo_enlace, Validador.Enlace.BLE)
        self.assertEqual(v.ultimo_hola["version_firmware"], 20261114)
        self.assertEqual(v.ultimo_hola["via"], "ble")
        self.assertLess(timezone.now() - v.ultima_sync, timedelta(seconds=5))

    def test_responde_acuse_al_dia_sin_listas(self):
        servicio.procesar(self.eventos(self.viaje(1)), "mqtt")
        al_dia = servicio.tarifas_actuales().version
        respuestas = servicio.procesar(self.hola(version_tarifas=al_dia), "mqtt")
        self.assertEqual([x.cabecera.tipo for x in self.leer(respuestas)], [p.ACUSE])
        self.assertEqual(self.acuse(respuestas), 1)

    def test_listas_pendientes_encadenadas_de_120_en_120(self):
        CambioLista.objects.bulk_create([CambioLista(tipo=1, op=1, a=i) for i in range(1, 251)])
        versiones = list(CambioLista.objects.values_list("version", flat=True))
        desde = versiones[4]
        listas = self.listas(servicio.procesar(self.hola(version_listas=desde), "mqtt"))
        self.assertEqual([len(x.entradas) for x in listas], [120, 120, 5])
        self.assertEqual(listas[0].version_desde, desde)
        for a, b in zip(listas, listas[1:]):
            self.assertEqual(b.version_desde, a.version_hasta)
        self.assertEqual(listas[-1].version_hasta, versiones[-1])
        self.assertEqual([e.a for x in listas for e in x.entradas], list(range(6, 251)))
        self.v[101].refresh_from_db()
        self.assertEqual(self.v[101].version_listas, desde)

    def test_huecos_de_la_secuencia_se_saltan(self):
        primera = CambioLista.objects.create(tipo=1, op=1, a=1).version
        CambioLista.objects.create(tipo=1, op=1, a=2).delete()   # como una transacción deshecha
        ultima = CambioLista.objects.create(tipo=1, op=1, a=3).version
        listas = self.listas(servicio.procesar(self.hola(version_listas=primera), "mqtt"))
        self.assertEqual([(x.version_desde, x.version_hasta, [e.a for e in x.entradas])
                          for x in listas], [(primera, ultima, [3])])

    def test_foto_completa_con_mas_de_2000_de_atraso(self):
        Tarjeta.objects.filter(pk=self.tarjeta.pk).update(estado=Tarjeta.Estado.BLOQUEADA)
        anulada = Tarjeta.objects.create(uid="04BB", cuenta=self.cuenta,
                                         estado=Tarjeta.Estado.ANULADA)
        Recarga.objects.create(cuenta=self.cuenta, seq=1, monto=700, origen="web")
        Recarga.objects.create(cuenta=self.cuenta, seq=2, monto=300, origen="web",
                               aplicada_en=timezone.now())
        CambioLista.objects.bulk_create([CambioLista(tipo=1, op=2, a=i) for i in range(2010)])
        actual = CambioLista.objects.order_by("-version").first().version

        (foto,) = self.listas(servicio.procesar(self.hola(version_listas=actual - 2001), "mqtt"))
        self.assertEqual((foto.version_desde, foto.version_hasta), (0, actual))
        self.assertEqual(set(foto.entradas), {
            p.EntradaLista(p.BLOQUEO, p.AGREGAR, self.tarjeta.id),
            p.EntradaLista(p.BLOQUEO, p.AGREGAR, anulada.id),
            p.EntradaLista(p.RECARGA, p.PENDIENTE, self.cuenta.id, 1, 700)})

        # Con 2000 justos, todavía van los cambios
        cambios = self.listas(servicio.procesar(self.hola(version_listas=actual - 2000), "mqtt"))
        self.assertEqual(cambios[0].version_desde, actual - 2000)

    def test_foto_completa_en_varios_paquetes_y_version_del_futuro(self):
        Tarjeta.objects.bulk_create([Tarjeta(uid=f"04{i:04X}", cuenta=self.cuenta,
                                             estado=Tarjeta.Estado.BLOQUEADA) for i in range(130)])
        CambioLista.objects.create(tipo=1, op=1, a=1)
        actual = CambioLista.objects.get().version
        # El validador dice ir por una versión que el servidor no tiene → foto completa
        a, b = self.listas(servicio.procesar(self.hola(version_listas=actual + 50), "mqtt"))
        self.assertEqual((a.version_desde, len(a.entradas)), (0, 120))
        self.assertEqual((b.version_desde, b.version_hasta, len(b.entradas)),
                         (a.version_hasta, actual, 10))
        self.assertGreaterEqual(a.version_hasta, 0x8000_0000, "intermedia: nunca una real")
        # Si se queda a medias, su HOLA trae la intermedia y recibe otra foto
        otra = self.listas(servicio.procesar(self.hola(version_listas=a.version_hasta), "mqtt"))
        self.assertEqual(otra[0].version_desde, 0)

    def test_tarifas_solo_si_cambiaron(self):
        version = servicio.tarifas_actuales().version
        respuestas = self.leer(servicio.procesar(self.hola(version_tarifas=0), "mqtt"))
        (tarifas,) = [x.cuerpo for x in respuestas if x.cabecera.tipo == p.TARIFAS]
        self.assertEqual(tarifas.version, version)
        self.assertEqual([(t.codigo, t.precio) for t in tarifas.tarifas],
                         list(Tarifa.objects.order_by("codigo").values_list("codigo", "precio")))

        al_dia = self.leer(servicio.procesar(self.hola(version_tarifas=version), "mqtt"))
        self.assertNotIn(p.TARIFAS, [x.cabecera.tipo for x in al_dia])

        t = Tarifa.objects.order_by("codigo").first()
        t.modificada = timezone.now() + timedelta(seconds=10)
        Tarifa.objects.filter(pk=t.pk).update(precio=t.precio + 10, modificada=t.modificada)
        nuevas = self.leer(servicio.procesar(self.hola(version_tarifas=version), "mqtt"))
        (tarifas,) = [x.cuerpo for x in nuevas if x.cabecera.tipo == p.TARIFAS]
        self.assertGreater(tarifas.version, version)
        self.assertIn(t.precio + 10, [x.precio for x in tarifas.tarifas])

    def test_respuestas_van_al_origen_con_secuencia_creciente(self):
        CambioLista.objects.create(tipo=1, op=1, a=1)
        respuestas = self.leer(servicio.procesar(self.hola(origen=102), "espnow", portador=101))
        self.assertTrue(all(x.cabecera.destino == 102 and x.cabecera.origen == 0
                            for x in respuestas))
        secuencias = [x.cabecera.secuencia for x in respuestas]
        self.assertEqual(secuencias, sorted(set(secuencias)))
        siguiente = self.leer(servicio.procesar(self.hola(), "mqtt"))[0].cabecera.secuencia
        self.assertGreater(siguiente, secuencias[-1])


class Invalidos(Base):
    def test_nunca_lanza_y_deja_una_alerta(self):
        bueno = self.eventos(self.viaje(1))
        Validador.objects.filter(numero=102).update(activo=False)
        casos = {
            "basura": b"\x00" * 3,
            "magia": b"XX" + bueno[2:],
            "cortado": bueno[:-1],
            "firma": bueno[:-1] + bytes([bueno[-1] ^ 1]),
            "validador inactivo": self.eventos(self.viaje(1), origen=102),
            "destino": self.eventos(self.viaje(1), destino=7),
        }
        for nombre, datos in casos.items():
            with self.subTest(nombre):
                antes = Alerta.objects.filter(tipo="paquete inválido").count()
                self.assertEqual(servicio.procesar(datos, "http"), [])
                self.assertEqual(Alerta.objects.filter(tipo="paquete inválido").count(), antes + 1)
        self.assertEqual(self.viajes().count(), 0)

    def test_paquete_del_servidor_se_rechaza(self):
        cab = p.Cabecera(p.ACUSE, origen=0, destino=0, secuencia=1, creado=AHORA)
        datos = p.empaquetar(cab, p.Acuse(5, AHORA), clave_ecdsa=CLAVE)
        self.assertEqual(servicio.procesar(datos, "mqtt"), [])
        self.assertEqual(len(self.alertas("paquete inválido")), 1)

    def test_via_desconocida_es_un_error_de_programacion(self):
        with self.assertRaises(ValueError):
            servicio.procesar(self.hola(), "4g")
