"""Delete comp rows from the comp database, children first.

`properties` has three child tables and NO referential integrity behind
them: the schema declares FOREIGN KEYs, but nothing enables SQLite's
`foreign_keys` pragma and no constraint carries ON DELETE CASCADE. So a
hand-written `DELETE FROM properties WHERE id = ...` in a shell strands
every child row — data.comp_db.save_analysis carries a comment about a
live DB that had accumulated 124 orphaned unit_mix and 279 orphaned
expense_lines rows exactly that way.

This command exists so the one maintenance operation anyone actually
needs is not a hand-written DELETE. It reuses save_analysis's ordering
rather than restating it.

    manage.py prune_comp --id 7 --dry-run
    manage.py prune_comp --pdf "Abilene_OM.pdf"
    manage.py prune_comp --orphans          # sweep already-stranded rows
"""
import sqlite3

from django.core.management.base import BaseCommand, CommandError

#: Children before parents. Same tuple, same order, as the upsert path in
#: data.comp_db.save_analysis — if one grows a table the other must too.
CHILD_TABLES = ("data_sources", "unit_mix", "expense_lines")


class Command(BaseCommand):
    help = "Delete a comp row (and its children) from the comp database"

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, help="properties.id to delete")
        parser.add_argument("--pdf", help="pdf_filename to delete (may match "
                                          "more than one row)")
        parser.add_argument("--orphans", action="store_true",
                            help="delete child rows whose property is gone")
        parser.add_argument("--dry-run", action="store_true",
                            help="report what would be deleted, change nothing")

    def handle(self, *args, **options):
        from data.comp_db import CompDatabase

        if sum(bool(options[k]) for k in ("id", "pdf", "orphans")) != 1:
            raise CommandError("give exactly one of --id, --pdf, --orphans")

        dry = options["dry_run"]
        db = CompDatabase()
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if options["orphans"]:
                self._orphans(conn, dry)
            else:
                self._rows(conn, options, dry)
            if dry:
                conn.rollback()
                self.stdout.write(self.style.WARNING(
                    "--dry-run: nothing was changed"))
            else:
                conn.commit()
        finally:
            conn.close()

    # ── row deletion ────────────────────────────────────────────────

    def _rows(self, conn, options, dry):
        if options["id"]:
            where, params = "id = ?", (options["id"],)
        else:
            where, params = "pdf_filename = ?", (options["pdf"],)
        rows = conn.execute(
            f"SELECT id, property_name, city, state, nrsf, adjusted_noi,"
            f" noi_per_sf, analysis_date, pdf_filename"
            f" FROM properties WHERE {where}", params).fetchall()
        if not rows:
            raise CommandError("no comp row matches that --id/--pdf")

        for row in rows:
            # Printed before the delete, and in full: this is the only
            # record of what was removed once the row is gone.
            self.stdout.write(
                f"id={row['id']}  {row['property_name']!r}  "
                f"{row['city']}, {row['state']}  "
                f"nrsf={row['nrsf']}  adjusted_noi={row['adjusted_noi']}  "
                f"noi_per_sf={row['noi_per_sf']}  "
                f"analyzed={row['analysis_date']}  pdf={row['pdf_filename']!r}")
            for table in CHILD_TABLES:
                n = conn.execute(
                    f"DELETE FROM {table} WHERE property_id = ?",
                    (row["id"],)).rowcount
                self.stdout.write(f"    {table}: {n} child row(s)")
            conn.execute("DELETE FROM properties WHERE id = ?", (row["id"],))
        self.stdout.write(self.style.SUCCESS(
            f"{'would delete' if dry else 'deleted'} {len(rows)} comp row(s)"))

    # ── orphan sweep ────────────────────────────────────────────────

    def _orphans(self, conn, dry):
        total = 0
        for table in CHILD_TABLES:
            n = conn.execute(
                f"DELETE FROM {table} WHERE property_id NOT IN "
                f"(SELECT id FROM properties)").rowcount
            self.stdout.write(f"{table}: {n} orphan(s)")
            total += n
        self.stdout.write(self.style.SUCCESS(
            f"{'would delete' if dry else 'deleted'} {total} orphan(s)"))
