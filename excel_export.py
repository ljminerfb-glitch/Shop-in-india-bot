"""Automatic, non-destructive SQLite-to-Excel snapshot export."""
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

log = logging.getLogger("shop_excel_export")
_export_lock = threading.RLock()
_SENSITIVE_COLUMNS = {
    "password", "password_hash", "password_salt", "token", "bot_token",
    "api_key", "secret", "private_key", "recovery_code",
}


def _safe_cell(value):
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _sheet_name(table, used):
    base = re.sub(r"[\\/*?:\[\]]", "_", str(table))[:31] or "Records"
    name = base
    suffix = 2
    while name.casefold() in used:
        tail = f"_{suffix}"
        name = base[:31-len(tail)] + tail
        suffix += 1
    used.add(name.casefold())
    return name


def export_database(db_path=None, export_dir=None):
    """Write a complete workbook snapshot; failures never affect the SQLite DB."""
    db_path = db_path or os.getenv("SHOP_DB", "shop_in_india.db")
    export_dir = Path(export_dir or os.getenv("EXCEL_EXPORT_DIR", "data_exports"))
    target = export_dir / "shop_records.xlsx"
    temp = export_dir / ".shop_records.tmp.xlsx"
    with _export_lock:
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
            source = sqlite3.connect(str(db_path), timeout=30)
            source.row_factory = sqlite3.Row
            try:
                tables = source.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                workbook = Workbook()
                index = workbook.active
                index.title = "Export Index"
                index.append(["Database table", "Excel sheet", "Exported rows"])
                used = {"export index"}
                for table_row in tables:
                    table = table_row["name"]
                    columns = source.execute(f'PRAGMA table_info("{table.replace(chr(34), chr(34)*2)}")').fetchall()
                    visible = [col["name"] for col in columns
                               if col["name"].casefold() not in _SENSITIVE_COLUMNS]
                    if not visible:
                        continue
                    sheet_name = _sheet_name(table, used)
                    sheet = workbook.create_sheet(sheet_name)
                    sheet.append(visible)
                    quoted_table = '"' + table.replace('"', '""') + '"'
                    quoted_columns = ",".join('"' + col.replace('"', '""') + '"' for col in visible)
                    rows = source.execute(f"SELECT {quoted_columns} FROM {quoted_table}")
                    count = 0
                    for row in rows:
                        sheet.append([_safe_cell(row[col]) for col in visible])
                        count += 1
                    sheet.freeze_panes = "A2"
                    sheet.auto_filter.ref = sheet.dimensions
                    for cell in sheet[1]:
                        cell.font = Font(bold=True, color="FFFFFF")
                        cell.fill = PatternFill("solid", fgColor="1F4E78")
                    for col_idx, col_name in enumerate(visible, 1):
                        sample_width = min(max(len(str(col_name)), 12), 32)
                        sheet.column_dimensions[get_column_letter(col_idx)].width = sample_width
                    index.append([table, sheet_name, count])
                index.freeze_panes = "A2"
                for cell in index[1]:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="1F4E78")
                index.column_dimensions["A"].width = 30
                index.column_dimensions["B"].width = 24
                index.column_dimensions["C"].width = 18
                workbook.save(temp)
            finally:
                source.close()
            os.replace(temp, target)
            log.info("Excel snapshot updated: %s", target)
            return str(target)
        except Exception:
            log.exception("Excel export failed; SQLite records remain safe")
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
            return None


class ExportConnection(sqlite3.Connection):
    """SQLite connection that refreshes the workbook after every successful commit."""
    def commit(self):
        super().commit()
        export_database()
