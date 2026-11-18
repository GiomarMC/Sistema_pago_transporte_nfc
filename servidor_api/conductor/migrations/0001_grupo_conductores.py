from django.db import migrations


def crear_grupo(apps, schema_editor):
    apps.get_model("auth", "Group").objects.using(schema_editor.connection.alias).get_or_create(
        name="conductores"
    )


class Migration(migrations.Migration):
    dependencies = [("auth", "0012_alter_user_first_name_max_length")]
    operations = [migrations.RunPython(crear_grupo, migrations.RunPython.noop)]
