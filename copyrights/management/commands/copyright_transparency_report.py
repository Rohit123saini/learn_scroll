"""Write the transparency report to a file (or stdout) for the lawyer / the website.

  python manage.py copyright_transparency_report --year 2026 --half 1 --format csv --out h1_2026.csv
  python manage.py copyright_transparency_report --from 2026-01-01 --to 2026-03-31            # JSON to stdout
  python manage.py copyright_transparency_report --format html --out transparency.html

Works even when COPYRIGHT_TRANSPARENCY_PUBLIC is false. Read-only.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from copyrights import transparency


class Command(BaseCommand):
    help = "Build the copyright transparency report (claims / removals / restored ...) as json, csv or html."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int)
        parser.add_argument("--half", type=int, choices=(1, 2))
        parser.add_argument("--from", dest="date_from", help="first day, YYYY-MM-DD")
        parser.add_argument("--to", dest="date_to", help="last day (inclusive), YYYY-MM-DD")
        parser.add_argument("--format", choices=("json", "csv", "html"), default="json")
        parser.add_argument("--out", help="file to write (default: stdout)")

    def handle(self, *args, **opts):
        params = {"year": opts["year"], "half": opts["half"], "from": opts["date_from"], "to": opts["date_to"]}
        try:
            report = transparency.get_report(params, use_cache=False)
        except ValueError as exc:
            raise CommandError(str(exc))
        fmt = opts["format"]
        text = (json.dumps(report, indent=2) if fmt == "json"
                else transparency.render_csv(report) if fmt == "csv" else transparency.render_html(report))
        if opts["out"]:
            with open(opts["out"], "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            self.stdout.write(f"Wrote {opts['out']} ({fmt}, {report['period']['from']} .. {report['period']['to']})")
        else:
            self.stdout.write(text)
