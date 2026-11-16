"""M9-02: cada servicio registra su CambioLista en la misma transacción."""

from unittest import mock

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from transporte import servicios
from transporte.models import CambioLista, Cuenta, Recarga, Tarifa, Tarjeta

BLOQUEO, RECARGA = CambioLista.Tipo.BLOQUEO, CambioLista.Tipo.RECARGA
AGREGAR, PENDIENTE, APLICADA = CambioLista.AGREGAR, CambioLista.PENDIENTE, CambioLista.APLICADA


class Fallo(Exception):
    pass


class Base(TestCase):
    def setUp(self):
        tarifa = Tarifa.objects.get_or_create(codigo=1, defaults={"nombre": "General",
                                                                   "precio": 100})[0]
        self.cuenta = Cuenta.objects.create(nombre="Ana", documento="45678912", tarifa=tarifa)

    def tarjeta(self, uid="04AA", estado=Tarjeta.Estado.ACTIVA, cuenta=None):
        return Tarjeta.objects.create(uid=uid, cuenta=cuenta or self.cuenta, estado=estado)

    def cambios(self, desde=0):
        return [(c.tipo, c.op, c.a, c.b, c.c, c.urgente)
                for c in CambioLista.objects.filter(version__gt=desde).order_by("version")]

    def ultima_version(self):
        ultimo = CambioLista.objects.order_by("-version").first()
        return ultimo.version if ultimo else 0


class Bloqueos(Base):
    def test_bloquear_registra_bloqueo_urgente(self):
        t = self.tarjeta()
        servicios.bloquear(t, "robo", tipo="bloqueo manual")
        self.assertEqual(self.cambios(), [(BLOQUEO, AGREGAR, t.id, 0, 0, True)])
        self.assertEqual(t.estado, Tarjeta.Estado.BLOQUEADA)

    def test_bloquear_dos_veces_registra_un_solo_cambio(self):
        t = self.tarjeta()
        copia = Tarjeta.objects.get(pk=t.pk)      # otra vista que la leyó activa
        servicios.bloquear(t, "robo")
        servicios.bloquear(copia, "robo otra vez")
        self.assertEqual(len(self.cambios()), 1)
        self.assertEqual(copia.estado, Tarjeta.Estado.BLOQUEADA, "se refresca el objeto")

    def test_bloquear_tarjeta_no_activa_no_registra(self):
        for estado in (Tarjeta.Estado.PENDIENTE, Tarjeta.Estado.ANULADA):
            servicios.bloquear(self.tarjeta(uid=estado, estado=estado), "x")
        self.assertEqual(self.cambios(), [])

    def test_anular_por_uid_registra_cada_emision_como_urgente(self):
        a = self.tarjeta(uid="04BB")
        p = self.tarjeta(uid="04BB", estado=Tarjeta.Estado.PENDIENTE)
        self.tarjeta(uid="04BB", estado=Tarjeta.Estado.BLOQUEADA)  # ya en la lista
        self.tarjeta(uid="04CC")
        self.assertEqual(servicios.anular_por_uid("04BB"), 2)
        self.assertEqual(self.cambios(), [(BLOQUEO, AGREGAR, a.id, 0, 0, True),
                                          (BLOQUEO, AGREGAR, p.id, 0, 0, True)])

    def test_confirmar_emision_registra_anuladas_y_reemplazadas(self):
        anterior = self.tarjeta(uid="04DD")                    # misma física, emisión vieja
        otra = self.tarjeta(uid="04EE")                        # otra tarjeta de la cuenta
        nueva = servicios.reservar_emision("04DD", id_cuenta=self.cuenta.id)
        _, reemplazadas = servicios.confirmar_emision(nueva.id)
        self.assertEqual([t.id for t in reemplazadas], [otra.id])
        self.assertEqual(self.cambios(), [
            (BLOQUEO, AGREGAR, anterior.id, 0, 0, False),      # se regraba encima: no urgente
            (BLOQUEO, AGREGAR, otra.id, 0, 0, True),
        ])

    def test_fraude_en_sincronizacion_registra_bloqueo(self):
        t = self.tarjeta()
        validador = mock.Mock(numero=101)
        servicios.sincronizar(validador, [{"id": 1, "tipo": "fraude", "tarjeta_id": t.id,
                                           "detalle": "clon"}])
        self.assertEqual(self.cambios(), [(BLOQUEO, AGREGAR, t.id, 0, 0, True)])


class Recargas(Base):
    def test_crear_recarga_registra_pendiente(self):
        r = servicios.crear_recarga(self.cuenta.id, 1000, "web")
        self.assertEqual(self.cambios(), [(RECARGA, PENDIENTE, self.cuenta.id, r.seq, 1000, False)])

    def test_crear_recarga_invalida_no_registra(self):
        with self.assertRaises(servicios.ErrorNegocio):
            servicios.crear_recarga(self.cuenta.id, 5, "web")
        self.assertEqual(self.cambios(), [])

    def test_entregar_recargas_registra_aplicada_por_cada_una(self):
        t = self.tarjeta()
        r1 = servicios.crear_recarga(self.cuenta.id, 500, "web")
        r2 = servicios.crear_recarga(self.cuenta.id, 700, "web")
        r3 = servicios.crear_recarga(self.cuenta.id, 900, "web")
        v = self.ultima_version()
        self.assertEqual(servicios.entregar_recargas(t, r2.seq, 101), 2)
        self.assertEqual(self.cambios(v), [(RECARGA, APLICADA, self.cuenta.id, r1.seq, 500, False),
                                           (RECARGA, APLICADA, self.cuenta.id, r2.seq, 700, False)])
        # Entregar otra vez las mismas no hace nada; la siguiente sí
        v = self.ultima_version()
        self.assertEqual(servicios.entregar_recargas(t, r2.seq, 102), 0)
        self.assertEqual(self.cambios(v), [])
        servicios.entregar_recargas(t, r3.seq, 102)
        self.assertEqual(self.cambios(v), [(RECARGA, APLICADA, self.cuenta.id, r3.seq, 900, False)])

    def test_entregar_en_tarjeta_no_activa_no_registra(self):
        t = self.tarjeta(estado=Tarjeta.Estado.BLOQUEADA)
        r = servicios.crear_recarga(self.cuenta.id, 500, "web")
        v = self.ultima_version()
        self.assertEqual(servicios.entregar_recargas(t, r.seq, 101), 0)
        self.assertEqual(self.cambios(v), [])

    def test_registrar_entrega_en_punto_registra_aplicada(self):
        t = self.tarjeta()
        r = servicios.crear_recarga(self.cuenta.id, 1500, "punto")
        v = self.ultima_version()
        servicios.registrar_entrega_en_punto(t, r.seq, operacion=4, contador=9, recarga_id=r.id)
        self.assertEqual(self.cambios(v), [(RECARGA, APLICADA, self.cuenta.id, r.seq, 1500, False)])

    def test_confirmar_emision_entrega_las_recargas_previas(self):
        r = servicios.crear_recarga(self.cuenta.id, 800, "web")
        nueva = servicios.reservar_emision("04FF", id_cuenta=self.cuenta.id)
        v = self.ultima_version()
        servicios.confirmar_emision(nueva.id)
        self.assertEqual(self.cambios(v), [(RECARGA, APLICADA, self.cuenta.id, r.seq, 800, False)])

    def test_viaje_con_recarga_hasta_registra_aplicada(self):
        t = self.tarjeta()
        r = servicios.crear_recarga(self.cuenta.id, 600, "web")
        v = self.ultima_version()
        servicios.sincronizar(mock.Mock(numero=101), [{
            "id": 1, "tipo": "viaje", "fecha": timezone.now().isoformat(), "tarjeta_id": t.id,
            "uid": t.uid, "monto": 100, "operacion": 2, "contador": 5, "recarga_hasta": r.seq}])
        self.assertEqual(self.cambios(v), [(RECARGA, APLICADA, self.cuenta.id, r.seq, 600, False)])


class Atomicidad(Base):
    """Si la operación falla, su CambioLista tampoco queda."""

    def test_bloquear_falla_despues_de_registrar(self):
        t = self.tarjeta()
        with mock.patch.object(servicios, "alertar", side_effect=Fallo), self.assertRaises(Fallo):
            servicios.bloquear(t, "robo")
        self.assertEqual(self.cambios(), [])
        self.assertEqual(Tarjeta.objects.get(pk=t.pk).estado, Tarjeta.Estado.ACTIVA)

    def test_confirmar_emision_falla_al_final(self):
        self.tarjeta(uid="04DD")
        self.tarjeta(uid="04EE")
        servicios.crear_recarga(self.cuenta.id, 800, "web")
        nueva = servicios.reservar_emision("04DD", id_cuenta=self.cuenta.id)
        v = self.ultima_version()
        with mock.patch.object(servicios, "registrar_movimiento", side_effect=Fallo), \
                self.assertRaises(Fallo):
            servicios.confirmar_emision(nueva.id)
        self.assertEqual(self.cambios(v), [])
        self.assertEqual(Tarjeta.objects.filter(estado=Tarjeta.Estado.ACTIVA).count(), 2)
        self.assertFalse(Recarga.objects.filter(aplicada_en__isnull=False).exists())

    def test_cada_servicio_dentro_de_una_transaccion_que_falla(self):
        t = self.tarjeta()
        r = servicios.crear_recarga(self.cuenta.id, 500, "web")
        v = self.ultima_version()
        operaciones = {
            "crear_recarga": lambda: servicios.crear_recarga(self.cuenta.id, 500, "web"),
            "entregar_recargas": lambda: servicios.entregar_recargas(t, r.seq, 101),
            "registrar_entrega_en_punto": lambda: servicios.registrar_entrega_en_punto(
                t, r.seq, 3, 7),
            "anular_por_uid": lambda: servicios.anular_por_uid(t.uid),
            "bloquear": lambda: servicios.bloquear(Tarjeta.objects.get(pk=t.pk), "x"),
        }
        for nombre, operacion in operaciones.items():
            with self.subTest(nombre):
                antes = CambioLista.objects.count()
                with self.assertRaises(Fallo), transaction.atomic():
                    operacion()
                    self.assertGreater(CambioLista.objects.count(), antes, "registró algo")
                    raise Fallo
                self.assertEqual(self.cambios(v), [])


class CambiosDesde(Base):
    def test_devuelve_los_posteriores_en_orden_y_con_limite(self):
        for i in range(5):
            servicios.crear_recarga(self.cuenta.id, 100 + i, "web")
        versiones = list(CambioLista.objects.order_by("version").values_list("version", flat=True))
        self.assertEqual([c.version for c in servicios.cambios_desde(0)], versiones)
        self.assertEqual([c.version for c in servicios.cambios_desde(versiones[1])], versiones[2:])
        self.assertEqual([c.c for c in servicios.cambios_desde(versiones[0], limite=2)], [101, 102])
        self.assertEqual(servicios.cambios_desde(versiones[-1]), [])

    def test_limite_por_defecto_es_un_paquete_listas(self):
        CambioLista.objects.bulk_create([CambioLista(tipo=1, op=1, a=i) for i in range(1, 131)])
        self.assertEqual(len(servicios.cambios_desde(0)), 120)
