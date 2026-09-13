"""Create Account #1 without the launcher's interactive prompt.

Run from an instance's own directory, naming its settings module:

    ../venv/bin/python <this> settings_shard0

Same call the launcher makes (evennia_launcher.create_superuser), just
without stdin. Evennia wants the superuser to exist before first start.
"""

import os
import sys

import django

sys.path.insert(0, os.getcwd())
os.environ["DJANGO_SETTINGS_MODULE"] = f"server.conf.{sys.argv[1]}"
django.setup()

import evennia  # noqa: E402

evennia._init()

from evennia.accounts.models import AccountDB  # noqa: E402

if AccountDB.objects.filter(id=1).exists():
    print(f"{sys.argv[1]}: account #1 already exists")
else:
    account = AccountDB.objects.create_superuser("root", "root@example.com", "p")
    account.save()
    print(f"{sys.argv[1]}: created #{account.id} {account.username}")
