import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Crée une sauvegarde cohérente de la base SQLite ou MariaDB configurée."

    def add_arguments(self, parser):
        parser.add_argument("--directory", default=os.getenv("BACKUP_DIR", "/backups"), help="Dossier de destination")

    def handle(self, *args, **options):
        os.umask(0o077)
        destination = Path(options["directory"]).expanduser()
        destination.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        engine = settings.DATABASES["default"]["ENGINE"]
        if engine.endswith("sqlite3"):
            target = destination / f"naya-marina-{stamp}.sqlite3"
            import sqlite3

            source_path = Path(settings.DATABASES["default"]["NAME"])
            source = sqlite3.connect(f"file:{source_path.resolve().as_posix()}?mode=ro", uri=True)
            backup = sqlite3.connect(target)
            try:
                source.backup(backup)
            finally:
                backup.close()
                source.close()
        elif "mysql" in engine:
            target = destination / f"naya-marina-{stamp}.sql"
            database = settings.DATABASES["default"]
            environment = os.environ.copy()
            environment["MYSQL_PWD"] = database["PASSWORD"]
            dump_binary = shutil.which("mariadb-dump") or shutil.which("mysqldump")
            if not dump_binary:
                raise CommandError("Le client mysqldump/mariadb-dump est introuvable dans le conteneur.")
            command = [
                dump_binary, "--single-transaction", "--routines", "--events",
                "--no-tablespaces", "--host", database["HOST"], "--port", str(database["PORT"]),
                "--user", database["USER"], database["NAME"],
            ]
            try:
                with target.open("wb") as output:
                    subprocess.run(command, env=environment, stdout=output, stderr=subprocess.PIPE, check=True, timeout=300)
            except subprocess.CalledProcessError as exc:
                target.unlink(missing_ok=True)
                raise CommandError(f"La sauvegarde MariaDB a échoué (code {exc.returncode}).") from exc
            except subprocess.TimeoutExpired as exc:
                target.unlink(missing_ok=True)
                raise CommandError("La sauvegarde MariaDB a dépassé 5 minutes.") from exc
        else:
            raise CommandError("Moteur de base non pris en charge pour la sauvegarde.")
        self.stdout.write(self.style.SUCCESS(f"Sauvegarde créée : {target}"))
