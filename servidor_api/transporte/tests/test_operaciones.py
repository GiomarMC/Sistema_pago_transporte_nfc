"""#237: operaciones de tarjeta que faltan en el servidor, y la operación de las recargas aplicadas con --aplicar."""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from transporte import servicios
from transporte.models import Alerta, Movimiento, Recarga
from transporte.tests.test_api import Base

ALERTA = servicios.ALERTA_OPERACION_FALTANTE


class AplicarRecargaTests(Base):
    def recarga_en_punto_sin_grabar(self, t, monto=300):
        """El punto abona la recarga, pero la tarjeta se retira antes de grabarla: no hay entrega"""
        r = self.op.post("/api/recargas/", {"tarjeta": t.id, "uid": t.uid, "monto": monto,
                                            "recarga_en_tarjeta": 1}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data["recarga"]["id"]

    def aplicar(self, t, hasta_seq, operacion):
        """recargar.py --aplicar: graba lo pendiente y confirma sin recarga nueva"""
        r = self.op.post("/api/recargas/entregas/", {"tarjeta": t.id, "hasta_seq": hasta_seq,
                                                     "operacion": operacion, "contador": 9}, format="json")
        self.assertEqual(r.status_code, 200, r.data)

    def test_aplicar_anota_la_operacion_en_la_recarga_que_entrega(self):
        t = self.emitir()
        id_recarga = self.recarga_en_punto_sin_grabar(t)
        self.aplicar(t, hasta_seq=2, operacion=2)
        m = Movimiento.objects.get(ref=f"R{id_recarga}")
        self.assertEqual((m.operacion, m.validador, m.tarjeta_id, m.contador), (2, servicios.PUNTO_RECARGA, t.id, 9))
        self.assertFalse(Recarga.objects.filter(aplicada_en__isnull=True).exists())

    def test_aplicar_con_varias_pendientes_anota_la_ultima(self):
        t = self.emitir()
        self.op.post("/api/recargas/", {"documento": "71234567", "monto": 500}, format="json")  # remota, seq 2
        id_punto = self.recarga_en_punto_sin_grabar(t)  # seq 3
        self.aplicar(t, hasta_seq=3, operacion=2)
        self.assertEqual(Movimiento.objects.get(ref=f"R{id_punto}").operacion, 2)
        self.assertEqual(Movimiento.objects.filter(tipo="recarga", operacion=2).count(), 1)

    def test_aplicar_sin_pendientes_no_toca_nada(self):
        t = self.emitir()
        self.aplicar(t, hasta_seq=1, operacion=2)  # la seq 1 ya está aplicada
        self.assertFalse(Movimiento.objects.filter(tipo="recarga", operacion__isnull=False).exists())

    def test_con_recarga_nueva_sigue_igual(self):
        t = self.emitir()
        r = self.op.post("/api/recargas/", {"tarjeta": t.id, "uid": t.uid, "monto": 300,
                                            "recarga_en_tarjeta": 1}, format="json")
        self.op.post("/api/recargas/entregas/", {"tarjeta": t.id, "hasta_seq": 2, "operacion": 2, "contador": 4,
                                                 "recarga": r.data["recarga"]["id"]}, format="json")
        self.assertEqual(Movimiento.objects.get(ref=f"R{r.data['recarga']['id']}").operacion, 2)


class RevisarOperacionesTests(Base):
    def setUp(self):
        super().setUp()
        self.t = self.emitir()  # la emisión es la operación 1
        self.hace_dos_dias = timezone.now() - timedelta(days=2)

    def test_salto_viejo_deja_una_alerta_y_solo_una(self):
        # Como la tarjeta 1 el 23 nov: operaciones 2 y 4, la 3 se grabó en la tarjeta y no llegó
        self.sync(101, [self.viaje(self.t, 1, 2, 5, fecha=self.hace_dos_dias),
                        self.viaje(self.t, 2, 4, 7, fecha=self.hace_dos_dias)])
        self.assertEqual(servicios.revisar_operaciones(), [(self.t.id, 3)])
        alerta = Alerta.objects.get(tipo=ALERTA)
        self.assertEqual(alerta.tarjeta_id, self.t.id)
        self.assertTrue(alerta.detalle.startswith(f"tarjeta {self.t.id}, operación 3:"))
        self.assertIn("V101-2", alerta.detalle)
        self.assertEqual(servicios.revisar_operaciones(), [])  # otra pasada del cron: nada nuevo
        self.assertEqual(Alerta.objects.filter(tipo=ALERTA).count(), 1)

    def test_salto_reciente_espera(self):
        # Los buses suben cuando pueden: la 3 puede estar en otro bus que aún no sincronizó
        self.sync(101, [self.viaje(self.t, 1, 2, 5), self.viaje(self.t, 2, 4, 7)])
        self.assertEqual(servicios.revisar_operaciones(), [])
        self.assertEqual(servicios.revisar_operaciones(ahora=timezone.now() + timedelta(hours=25)), [(self.t.id, 3)])

    def test_la_que_faltaba_llega_tarde(self):
        self.sync(101, [self.viaje(self.t, 1, 2, 5), self.viaje(self.t, 2, 4, 7)])
        self.sync(102, [self.viaje(self.t, 1, 3, 6)])  # otro bus, más tarde
        self.assertEqual(servicios.revisar_operaciones(ahora=timezone.now() + timedelta(days=2)), [])

    def test_sin_saltos_ni_alertas(self):
        self.sync(101, [self.viaje(self.t, i, i + 1, i + 4, fecha=self.hace_dos_dias) for i in range(1, 6)])
        self.assertEqual(servicios.revisar_operaciones(), [])

    def test_salto_de_varias_y_otra_tarjeta(self):
        otra = self.emitir(uid="04112233445566", documento="70000001")
        self.sync(101, [self.viaje(self.t, 1, 2, 5, fecha=self.hace_dos_dias),
                        self.viaje(self.t, 2, 5, 8, fecha=self.hace_dos_dias),
                        self.viaje(otra, 3, 2, 5, fecha=self.hace_dos_dias),
                        self.viaje(otra, 4, 3, 6, fecha=self.hace_dos_dias)])
        self.assertEqual(servicios.revisar_operaciones(), [(self.t.id, 3), (self.t.id, 4)])

    def test_recarga_aplicada_no_es_un_salto(self):
        # Como la operación 14 de la tarjeta 1: recarga en punto que no se grabó y luego --aplicar
        self.sync(101, [self.viaje(self.t, 1, 2, 5, fecha=self.hace_dos_dias)])
        r = self.op.post("/api/recargas/", {"tarjeta": self.t.id, "uid": self.t.uid, "monto": 300,
                                            "recarga_en_tarjeta": 1}, format="json")
        self.op.post("/api/recargas/entregas/", {"tarjeta": self.t.id, "hasta_seq": r.data["hasta_seq"],
                                                 "operacion": 3, "contador": 6}, format="json")
        self.sync(101, [self.viaje(self.t, 2, 4, 7)])
        self.assertEqual(servicios.revisar_operaciones(ahora=timezone.now() + timedelta(days=2)), [])

    def test_comando(self):
        self.sync(101, [self.viaje(self.t, 1, 2, 5, fecha=self.hace_dos_dias),
                        self.viaje(self.t, 2, 4, 7, fecha=self.hace_dos_dias)])
        salida = StringIO()
        call_command("revisar_operaciones", stdout=salida)
        self.assertIn(f"Tarjeta {self.t.id}: falta la operación 3", salida.getvalue())
        self.assertIn("1 operaciones sin registrar", salida.getvalue())
        salida = StringIO()
        call_command("revisar_operaciones", "--horas", "100", stdout=salida)
        self.assertIn("0 operaciones", salida.getvalue())
