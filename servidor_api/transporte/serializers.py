from rest_framework import serializers

from .models import Cuenta, Movimiento, Recarga, Tarjeta


# --- Entrada ------------------------------------------------------------------

class EventoSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    tipo = serializers.ChoiceField(choices=["viaje", "incidencia", "fraude"])
    fecha = serializers.DateTimeField()
    tarjeta_id = serializers.IntegerField()
    uid = serializers.CharField(max_length=20)
    monto = serializers.IntegerField(required=False, allow_null=True)
    saldo_final = serializers.IntegerField(required=False, allow_null=True)
    operacion = serializers.IntegerField(required=False, allow_null=True)
    contador = serializers.IntegerField(required=False, allow_null=True)
    detalle = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    recarga_hasta = serializers.IntegerField(required=False, allow_null=True)


class SyncSerializer(serializers.Serializer):
    eventos = serializers.ListField(child=serializers.DictField(), allow_empty=True)


class EmisionSerializer(serializers.Serializer):
    uid = serializers.RegexField(r"^[0-9A-F]{8,20}$")
    cuenta = serializers.IntegerField(required=False)
    nombre = serializers.CharField(max_length=120, required=False)
    documento = serializers.CharField(max_length=20, required=False)
    tarifa = serializers.IntegerField(required=False, help_text="código de tarifa")

    def validate(self, datos):
        if "cuenta" not in datos and not all(k in datos for k in ("nombre", "documento", "tarifa")):
            raise serializers.ValidationError(
                "indica 'cuenta', o bien 'nombre', 'documento' y 'tarifa' para un cliente nuevo")
        return datos


class ConfirmarEmisionSerializer(serializers.Serializer):
    contador = serializers.IntegerField(required=False, allow_null=True)


class RecargaSerializer(serializers.Serializer):
    """Remota: documento + monto. En punto: tarjeta + uid + última recarga en la
    tarjeta (+ monto, o 0 para solo entregar las pendientes)."""
    monto = serializers.IntegerField(min_value=0, help_text="céntimos")
    origen = serializers.CharField(max_length=20, default="yape")
    documento = serializers.CharField(max_length=20, required=False)
    tarjeta = serializers.IntegerField(required=False)
    uid = serializers.CharField(max_length=20, required=False)
    recarga_en_tarjeta = serializers.IntegerField(required=False, min_value=0)

    def validate(self, datos):
        remota = "documento" in datos
        punto = "tarjeta" in datos
        if remota == punto:
            raise serializers.ValidationError("indica 'documento' (remota) o 'tarjeta' (punto)")
        if punto and ("uid" not in datos or "recarga_en_tarjeta" not in datos):
            raise serializers.ValidationError("en punto de recarga hacen falta 'uid' y "
                                              "'recarga_en_tarjeta'")
        if remota and datos["monto"] == 0:
            raise serializers.ValidationError("la recarga remota necesita un monto")
        return datos


class EntregaSerializer(serializers.Serializer):
    tarjeta = serializers.IntegerField()
    hasta_seq = serializers.IntegerField(min_value=1)
    operacion = serializers.IntegerField()
    contador = serializers.IntegerField(required=False, allow_null=True)
    recarga = serializers.IntegerField(required=False, allow_null=True)


class BloqueoSerializer(serializers.Serializer):
    motivo = serializers.CharField(max_length=200, default="reporte de robo o pérdida")


# --- Salida -------------------------------------------------------------------

class RecargaSalida(serializers.ModelSerializer):
    class Meta:
        model = Recarga
        fields = ["id", "seq", "monto", "origen", "creada", "aplicada_en", "aplicada_por"]


class MovimientoSalida(serializers.ModelSerializer):
    class Meta:
        model = Movimiento
        fields = ["fecha", "tipo", "monto", "saldo_final", "tarjeta", "validador", "operacion"]


class CuentaSalida(serializers.ModelSerializer):
    tarifa = serializers.IntegerField(source="tarifa_id")
    tarifa_nombre = serializers.CharField(source="tarifa.nombre")

    class Meta:
        model = Cuenta
        fields = ["id", "nombre", "documento", "tarifa", "tarifa_nombre", "saldo", "creada"]


class TarjetaSalida(serializers.ModelSerializer):
    cuenta = CuentaSalida()

    class Meta:
        model = Tarjeta
        fields = ["id", "uid", "estado", "emitida", "bloqueada_en", "recarga_inicial", "cuenta"]
