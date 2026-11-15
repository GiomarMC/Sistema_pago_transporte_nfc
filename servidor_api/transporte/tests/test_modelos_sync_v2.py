"""M9-01: modelos de la sincronización v2 y versiones iniciales de las listas."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from transporte.models import CambioLista, EventoRecibido, Tarifa, Validador
from transporte.sync_v2 import paquetes as p


class Modelos(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create(username="v101")
        self.v = Validador.objects.create(numero=101, usuario=usuario)

    def test_validador_empieza_sin_sincronizar(self):
        self.assertEqual((self.v.ultimo_evento_contiguo, self.v.version_listas), (0, 0))
        self.assertIsNone(self.v.ultimo_hola)
        self.assertIsNone(self.v.ultimo_enlace)
        self.assertEqual(self.v.mqtt_clave, "")

    def test_enlace_usa_los_codigos_del_hola(self):
        self.assertEqual([Validador.Enlace.WIFI, Validador.Enlace.BLE, Validador.Enlace.REENVIADO],
                         [p.WIFI, p.BLE, p.REENVIADO])

    def test_evento_recibido_es_unico_por_validador(self):
        EventoRecibido.objects.create(validador=self.v, id_evento=7, via="mqtt")
        otro = Validador.objects.create(
            numero=102, usuario=get_user_model().objects.create(username="v102"))
        EventoRecibido.objects.create(validador=otro, id_evento=7, via="http")
        EventoRecibido.objects.create(validador=self.v, id_evento=0xFFFFFFFF, via="ble")
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventoRecibido.objects.create(validador=self.v, id_evento=7, via="espnow")

    def test_cambios_lista_tienen_version_creciente(self):
        c1 = CambioLista.objects.create(tipo=CambioLista.Tipo.BLOQUEO, op=CambioLista.AGREGAR,
                                        a=5, urgente=True)
        c2 = CambioLista.objects.create(tipo=CambioLista.Tipo.RECARGA, op=CambioLista.PENDIENTE,
                                        a=1, b=3, c=1000)
        self.assertGreater(c2.version, c1.version)
        self.assertEqual(list(CambioLista.objects.values_list("version", flat=True)),
                         [c1.version, c2.version])
        self.assertIsNone(c1.difundido_en)
        self.assertEqual([CambioLista.Tipo.BLOQUEO, CambioLista.Tipo.RECARGA],
                         [p.BLOQUEO, p.RECARGA])
        self.assertEqual([CambioLista.AGREGAR, CambioLista.QUITAR, CambioLista.PENDIENTE,
                          CambioLista.APLICADA], [p.AGREGAR, p.QUITAR, p.PENDIENTE, p.APLICADA])

    def test_cambio_entra_en_una_entrada_de_c1(self):
        c = CambioLista.objects.create(tipo=2, op=1, a=0xFFFFFFFF, b=1, c=20000)
        entrada = p.EntradaLista(c.tipo, c.op, c.a, c.b, c.c)
        self.assertEqual(p.EntradaLista.de_bytes(entrada.a_bytes()), entrada)

    def test_cambio_rechaza_valores_fuera_de_c1(self):
        for campos in ({"tipo": 3, "op": 1, "a": 1}, {"tipo": 1, "op": 0, "a": 1},
                       {"tipo": 1, "op": 1, "a": 0x1_0000_0000}):
            with self.subTest(campos), self.assertRaises(IntegrityError), transaction.atomic():
                CambioLista.objects.create(**campos)

    def test_tarifa_modificada_se_actualiza_al_guardar(self):
        t = Tarifa.objects.create(codigo=9, nombre="Prueba", precio=100)
        antes = t.modificada
        t.precio = 120
        t.save()
        t.refresh_from_db()
        self.assertGreater(t.modificada, antes)


class MigracionVersionesIniciales(TransactionTestCase):
    """Aplica 0008 sobre datos creados con los modelos de 0007."""

    previa = [("transporte", "0006_validador_clave_hmac")]
    antes = [("transporte", "0007_sync_v2_modelos")]
    despues = [("transporte", "0008_cambios_lista_iniciales")]

    def migrar(self, destino):
        ejecutor = MigrationExecutor(connection)
        ejecutor.loader.build_graph()
        ejecutor.migrate(destino)
        return ejecutor.loader.project_state(destino).apps

    def setUp(self):
        # Desde 0006: la tabla de CambioLista se crea de cero, como en producción,
        # y su secuencia empieza en 1 (los rollbacks de otros tests no la reinician).
        self.migrar(self.previa)

    def tearDown(self):
        self.migrar(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_versiones_reproducen_el_estado_actual(self):
        apps = self.migrar(self.antes)
        Tarifa = apps.get_model("transporte", "Tarifa")
        Cuenta = apps.get_model("transporte", "Cuenta")
        Tarjeta = apps.get_model("transporte", "Tarjeta")
        Recarga = apps.get_model("transporte", "Recarga")

        general = Tarifa.objects.create(codigo=1, nombre="General", precio=100)
        ana = Cuenta.objects.create(nombre="Ana", documento="45678912", tarifa=general)
        luis = Cuenta.objects.create(nombre="Luis", documento="40000001", tarifa=general)
        hoy = timezone.now()

        activa = Tarjeta.objects.create(uid="04AA", cuenta=ana, estado="activa")
        Tarjeta.objects.create(uid="04BB", cuenta=ana, estado="pendiente")
        robada = Tarjeta.objects.create(uid="04CC", cuenta=luis, estado="bloqueada",
                                        bloqueada_en=hoy - timedelta(days=3))
        anulada = Tarjeta.objects.create(uid="04DD", cuenta=ana, estado="anulada",
                                         bloqueada_en=hoy - timedelta(days=10))
        sin_fecha = Tarjeta.objects.create(uid="04EE", cuenta=luis, estado="bloqueada")

        aplicada = Recarga.objects.create(cuenta=ana, seq=1, monto=500, origen="punto",
                                          aplicada_en=hoy, tarjeta=activa)
        r2 = Recarga.objects.create(cuenta=ana, seq=2, monto=1000, origen="web")
        r1 = Recarga.objects.create(cuenta=luis, seq=1, monto=2000, origen="yape")
        # creada es auto_now_add: se fijan las fechas después
        Recarga.objects.filter(pk=r2.pk).update(creada=hoy - timedelta(days=5))
        Recarga.objects.filter(pk=r1.pk).update(creada=hoy - timedelta(days=1))
        Recarga.objects.filter(pk=aplicada.pk).update(creada=hoy - timedelta(days=20))
        Tarjeta.objects.filter(pk=sin_fecha.pk).update(emitida=hoy - timedelta(days=2))

        apps = self.migrar(self.despues)
        CambioLista = apps.get_model("transporte", "CambioLista")
        filas = list(CambioLista.objects.order_by("version").values_list(
            "version", "tipo", "op", "a", "b", "c", "urgente"))
        self.assertEqual(filas, [
            (1, 1, 1, anulada.id, 0, 0, False),      # hace 10 días
            (2, 2, 1, ana.id, 2, 1000, False),       # hace 5 días
            (3, 1, 1, robada.id, 0, 0, False),       # hace 3 días
            (4, 1, 1, sin_fecha.id, 0, 0, False),    # sin bloqueada_en: emitida hace 2 días
            (5, 2, 1, luis.id, 1, 2000, False),      # hace 1 día
        ])
        self.assertFalse(CambioLista.objects.filter(difundido_en__isnull=True).exists(),
                         "el historial no se publica como urgente")

        # Lo mismo que entrega hoy la v1 (servicios.sincronizar)
        negra = set(CambioLista.objects.filter(tipo=1).values_list("a", flat=True))
        self.assertEqual(negra, set(Tarjeta.objects.filter(
            estado__in=["bloqueada", "anulada"]).values_list("id", flat=True)))
        pendientes = set(CambioLista.objects.filter(tipo=2).values_list("a", "b", "c"))
        self.assertEqual(pendientes, set(Recarga.objects.filter(
            aplicada_en__isnull=True).values_list("cuenta_id", "seq", "monto")))

        # Se puede deshacer
        apps = self.migrar(self.antes)
        self.assertEqual(apps.get_model("transporte", "CambioLista").objects.count(), 0)
        self.assertEqual(apps.get_model("transporte", "Tarjeta").objects.count(), 5)

    def test_sin_datos_no_crea_versiones(self):
        apps = self.migrar(self.despues)
        self.assertEqual(apps.get_model("transporte", "CambioLista").objects.count(), 0)
