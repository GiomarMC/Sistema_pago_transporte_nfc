"""Abre el libro y cierra la ventana para escritores de saldo sin libro.

Se ejecuta en una transacción PostgreSQL: el bloqueo de Cuenta impide que una
escritura monetaria se intercale entre la foto de apertura y el guardián SQL.
"""

from django.db import migrations, models
from django.db.models import Max
import django.db.models.deletion
from django.utils import timezone


SQL = r"""
CREATE FUNCTION libro_guardar_asiento() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.confirmado THEN RAISE EXCEPTION 'asiento confirmado inmutable'; END IF;
    RETURN OLD;
  END IF;
  IF OLD.confirmado THEN RAISE EXCEPTION 'asiento confirmado inmutable'; END IF;
  IF NEW.confirmado THEN
    IF (SELECT count(*) FROM libro_partida WHERE asiento_id = NEW.id) < 2
       OR (SELECT coalesce(sum(monto), 0) FROM libro_partida WHERE asiento_id = NEW.id) <> 0 THEN
      RAISE EXCEPTION 'asiento desequilibrado o sin dos partidas';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER libro_asiento_inmutable BEFORE UPDATE OR DELETE ON libro_asiento
FOR EACH ROW EXECUTE FUNCTION libro_guardar_asiento();

CREATE FUNCTION libro_guardar_partida() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    -- Serializa con la confirmación para que no entre otra partida después
    -- de comprobar el equilibrio del asiento.
    PERFORM 1 FROM libro_asiento WHERE id = NEW.asiento_id FOR UPDATE;
    IF (SELECT confirmado FROM libro_asiento WHERE id = NEW.asiento_id) THEN
      RAISE EXCEPTION 'partidas de asiento confirmado inmutables';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'DELETE' THEN
    PERFORM 1 FROM libro_asiento WHERE id = OLD.asiento_id FOR UPDATE;
    IF NOT (SELECT confirmado FROM libro_asiento WHERE id = OLD.asiento_id) THEN
      RETURN OLD;
    END IF;
  END IF;
  RAISE EXCEPTION 'partida inmutable';
END $$;
CREATE TRIGGER libro_partida_inmutable BEFORE INSERT OR UPDATE OR DELETE ON libro_partida
FOR EACH ROW EXECUTE FUNCTION libro_guardar_partida();

CREATE FUNCTION libro_guardar_cuenta_contable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM libro_partida WHERE cuenta_contable_id = OLD.id) THEN
    RAISE EXCEPTION 'cuenta contable con partidas inmutable';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER libro_cuenta_contable_inmutable BEFORE UPDATE OR DELETE ON libro_cuentacontable
FOR EACH ROW EXECUTE FUNCTION libro_guardar_cuenta_contable();

CREATE FUNCTION libro_validar_confirmacion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT (SELECT confirmado FROM libro_asiento WHERE id = NEW.id) THEN
    RAISE EXCEPTION 'asiento sin confirmar al finalizar transacción';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER libro_confirmacion_obligatoria
AFTER INSERT ON libro_asiento DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION libro_validar_confirmacion();

CREATE FUNCTION libro_guardar_saldo() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' AND NEW.saldo <> 0
     AND current_setting('subepe.libro_escritura', true) IS DISTINCT FROM 'on' THEN
    RAISE EXCEPTION 'saldo inicial requiere apertura contable';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.saldo IS DISTINCT FROM OLD.saldo
     AND current_setting('subepe.libro_escritura', true) IS DISTINCT FROM 'on' THEN
    RAISE EXCEPTION 'saldo requiere escritura contable';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER libro_saldo_guardado BEFORE INSERT OR UPDATE ON transporte_cuenta
FOR EACH ROW EXECUTE FUNCTION libro_guardar_saldo();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS libro_saldo_guardado ON transporte_cuenta;
DROP FUNCTION IF EXISTS libro_guardar_saldo();
DROP TRIGGER IF EXISTS libro_confirmacion_obligatoria ON libro_asiento;
DROP FUNCTION IF EXISTS libro_validar_confirmacion();
DROP TRIGGER IF EXISTS libro_partida_inmutable ON libro_partida;
DROP FUNCTION IF EXISTS libro_guardar_partida();
DROP TRIGGER IF EXISTS libro_cuenta_contable_inmutable ON libro_cuentacontable;
DROP FUNCTION IF EXISTS libro_guardar_cuenta_contable();
DROP TRIGGER IF EXISTS libro_asiento_inmutable ON libro_asiento;
DROP FUNCTION IF EXISTS libro_guardar_asiento();
"""


def apertura(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("M10 requiere PostgreSQL para sus garantías de integridad")
    alias = schema_editor.connection.alias
    Cuenta = apps.get_model("transporte", "Cuenta")
    Contable = apps.get_model("libro", "CuentaContable")
    Asiento = apps.get_model("libro", "Asiento")
    Partida = apps.get_model("libro", "Partida")
    Corte = apps.get_model("libro", "CorteHistorico")
    Movimiento = apps.get_model("transporte", "Movimiento")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("LOCK TABLE transporte_cuenta IN SHARE ROW EXCLUSIVE MODE")
    fijas = (
        ("CAJA_PASARELA", "Caja de pasarela", "activo"),
        ("CAJA_VENTANILLA", "Caja de ventanilla", "activo"),
        ("PASAJES_POR_LIQUIDAR", "Pasajes pendientes de atribución/liquidación", "pasivo"),
        ("AJUSTES", "Contrapartida de apertura y ajustes autorizados", "activo"),
    )
    for codigo, nombre, naturaleza in fijas:
        Contable.objects.using(alias).get_or_create(codigo=codigo,
            defaults={"nombre": nombre, "naturaleza": naturaleza})
    ajustes = Contable.objects.using(alias).get(codigo="AJUSTES")
    for cuenta in Cuenta.objects.using(alias).order_by("pk").iterator(chunk_size=500):
        Corte.objects.using(alias).get_or_create(
            cuenta_id=cuenta.pk,
            defaults={"ultimo_movimiento_id": Movimiento.objects.using(alias)
                      .filter(cuenta_id=cuenta.pk).aggregate(m=Max("id"))["m"] or 0})
        cliente, _ = Contable.objects.using(alias).get_or_create(
            cuenta_cliente_id=cuenta.pk,
            defaults={"codigo": f"SALDO_CLIENTE_{cuenta.pk}",
                      "nombre": f"Saldo de cuenta {cuenta.pk}", "naturaleza": "pasivo"})
        if cuenta.saldo == 0:
            continue
        asiento, creado = Asiento.objects.using(alias).get_or_create(
            ref=f"apertura-{cuenta.pk}", defaults={"fecha": timezone.now(),
            "tipo": "apertura", "descripcion": "Saldo previo a M10"})
        if creado:
            Partida.objects.using(alias).bulk_create([
                Partida(asiento=asiento, cuenta_contable=ajustes, monto=cuenta.saldo),
                Partida(asiento=asiento, cuenta_contable=cliente, monto=-cuenta.saldo),
            ])
            Asiento.objects.using(alias).filter(pk=asiento.pk).update(confirmado=True)
        else:
            if not asiento.confirmado:
                raise RuntimeError(f"apertura sin confirmar para cuenta {cuenta.pk}")
            partidas = list(Partida.objects.using(alias).filter(asiento=asiento)
                            .order_by("cuenta_contable_id").values_list("cuenta_contable_id", "monto"))
            if sorted(partidas) != sorted([(ajustes.pk, cuenta.saldo), (cliente.pk, -cuenta.saldo)]):
                raise RuntimeError(f"apertura incompatible para cuenta {cuenta.pk}")


class Migration(migrations.Migration):
    dependencies = [("libro", "0002_asiento_confirmado"), ("transporte", "0006_sync_incremental")]
    operations = [migrations.CreateModel(
        name="CorteHistorico",
        fields=[("cuenta", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                                primary_key=True, serialize=False,
                                                to="transporte.cuenta")),
                ("ultimo_movimiento_id", models.BigIntegerField(default=0))]),
                  migrations.RunPython(apertura, migrations.RunPython.noop),
                  migrations.RunSQL(SQL, REVERSE_SQL)]
