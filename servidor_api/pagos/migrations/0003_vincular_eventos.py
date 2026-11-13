import json

from django.db import migrations


def vincular(apps, schema_editor):
    Evento = apps.get_model("pagos", "EventoPasarela")
    for evento in Evento.objects.using(schema_editor.connection.alias).filter(
            firma_valida=True, id_externo__isnull=True).iterator(chunk_size=500):
        try:
            externo = json.loads(bytes(evento.cuerpo)).get("id_externo")
        except (ValueError, TypeError):
            continue
        if isinstance(externo, str) and len(externo) <= 100:
            Evento.objects.using(schema_editor.connection.alias).filter(pk=evento.pk).update(id_externo=externo)


class Migration(migrations.Migration):
    dependencies = [("pagos", "0002_eventopasarela_id_externo")]
    operations = [migrations.RunPython(vincular, migrations.RunPython.noop)]
