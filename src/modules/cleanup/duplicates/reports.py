"""Streaming CSV and escaped HTML exports, including per-file action outcomes."""
import csv
from html import escape


def export_report(path, result, outcomes=(), *, html=False):
    outcome_map = {o.path: o for o in outcomes}
    headers = ("Group", "Path", "Size", "Modified ns", "Created ns", "Method",
               "Confidence", "Hash", "Hard links", "Action status", "Reason", "Freed bytes")

    def rows():
        exported = set()
        for group in result.groups:
            for entry in group.files:
                outcome = outcome_map.get(entry.path)
                exported.add(entry.path)
                yield (group.id + 1, entry.path, entry.size, entry.mtime_ns, entry.ctime_ns,
                       group.method, group.confidence, group.digest, entry.nlink,
                       outcome.status if outcome else "", outcome.reason if outcome else "",
                       outcome.freed if outcome else 0)
        for outcome in outcomes:
            if outcome.path not in exported:
                yield ("", outcome.path, "", "", "", "", "", "", "", outcome.status,
                       outcome.reason, outcome.freed)

    with open(path, "w", newline="", encoding="utf-8") as stream:
        if not html:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(headers)
            for row in rows():
                # Prevent spreadsheet formulas in attacker-controlled filenames.
                writer.writerow("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@"))
                                else v for v in row)
        else:
            stream.write('<!doctype html>\n<html lang="en"><meta charset="utf-8">'
                         '<title>Cleanup Duplicates</title><body><h1>Cleanup Duplicates</h1>'
                         '<p>Candidate and probable matches require exact comparison before removal. '
                         'Recycle Bin and same-volume moves free zero bytes.</p><table border="1"><thead><tr>')
            stream.write("".join(f"<th>{escape(h)}</th>" for h in headers) + "</tr></thead><tbody>\n")
            for row in rows():
                stream.write("<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row) + "</tr>\n")
            stream.write("</tbody></table></body></html>\n")
