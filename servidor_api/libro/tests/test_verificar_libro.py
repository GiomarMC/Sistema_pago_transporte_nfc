"""M10-05: comando verificar_libro."""

from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from libro import libro
from libro.models import Asiento, CuentaContable, Partida
from transporte import servicios
from transporte.models import Alerta, Cuenta, Tarifa, Tarjeta


def verificar():
    """Ejecuta el comando. Devuelve (código de salida, stdout, stderr)."""
    out, err = StringIO(), StringIO()
    try:
        call_command("verificar_libro", stdout=out, stderr=err)
        codigo = 0
    except CommandError as e:
        codigo = e.returncode
        err.write(str(e))
    return codigo, out.getvalue(), err.getvalue()


class VerificarLibro(TestCase):
    def setUp(self):
        tarifa = Tarifa.objects.order_by("codigo").first()
        self.ana = Cuenta.objects.create(nombre="Ana", documento="1", tarifa=tarifa)
        self.beto = Cuenta.objects.create(nombre="Beto", documento="2", tarifa=tarifa)
        tarjeta = Tarjeta.objects.create(uid="04AA", cuenta=self.ana,
                                         estado=Tarjeta.Estado.ACTIVA)
        # Operación normal: todo pasa por registrar_movimiento
        servicios.crear_recarga(self.ana.id, 1000, "web")
        servicios.crear_recarga(self.beto.id, 500, "punto")
        servicios.procesar_evento(101, {
            "id": 1, "tipo": "viaje", "fecha": timezone.now().isoformat(),
            "tarjeta_id": tarjeta.id, "monto": 130, "operacion": 2})

    def alertas(self):
        return list(Alerta.objects.filter(tipo="descuadre contable")
                    .values_list("detalle", flat=True))

    def test_operacion_normal_cuadra(self):
        codigo, out, _ = verificar()
        self.assertEqual(codigo, 0)
        self.assertIn("cuadra", out)
        self.assertEqual(self.alertas(), [])

    def test_saldo_cambiado_con_update_sql_se_detecta(self):
        with connection.cursor() as c:
            c.execute("UPDATE transporte_cuenta SET saldo = saldo + 500 WHERE id = %s",
                      [self.ana.id])

        codigo, _, err = verificar()

        self.assertEqual(codigo, 1)
        (alerta,) = self.alertas()
        self.assertIn(f"cuenta {self.ana.id}:", alerta)
        self.assertIn("S/ 13.70", alerta)      # Cuenta.saldo: 8.70 + 5.00
        self.assertIn("S/ 8.70", alerta)       # lo que dice el libro
        self.assertIn(f"cuenta {self.ana.id}:", err)

    def test_una_alerta_por_cuenta_descuadrada(self):
        Cuenta.objects.filter(pk__in=[self.ana.id, self.beto.id]).update(saldo=0)
        self.assertEqual(verificar()[0], 1)
        self.assertEqual(len(self.alertas()), 2)
        self.assertEqual([d[0] for d in libro.descuadres()], [self.ana.id, self.beto.id])

    def test_cliente_sin_movimientos_ni_saldo_cuadra(self):
        Cuenta.objects.create(nombre="Caro", documento="3", tarifa=self.ana.tarifa)
        self.assertEqual(verificar()[0], 0)

    def test_libro_que_no_suma_cero_se_detecta(self):
        # Un asiento cojo escrito por fuera de libro.registrar()
        asiento = Asiento.objects.create(tipo=Asiento.Tipo.AJUSTE, ref="A-cojo")
        Partida.objects.create(asiento=asiento, monto=100,
                               cuenta_contable=CuentaContable.objects.get(codigo="AJUSTES"))

        codigo, _, _ = verificar()

        self.assertEqual(codigo, 1)
        self.assertEqual(self.alertas(),
                         ["el libro no suma cero: las partidas suman +100 céntimos"])

    def test_cada_ejecucion_con_descuadre_vuelve_a_alertar(self):
        Cuenta.objects.filter(pk=self.ana.id).update(saldo=0)
        verificar()
        verificar()
        self.assertEqual(len(self.alertas()), 2)
