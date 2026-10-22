# SPDX-License-Identifier: BSD-3-Clause
import evennia.utils.picklefield
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SpawnRule",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("file", models.CharField(max_length=255)),
                ("rule_id", models.PositiveIntegerField()),
                ("rule", evennia.utils.picklefield.PickledObjectField()),
                ("last_spawn_time", models.FloatField(blank=True, null=True)),
                ("last_death_time", models.FloatField(blank=True, null=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("file", "rule_id"),
                        name="mob_spawner_unique_file_rule",
                    ),
                ],
            },
        ),
    ]
