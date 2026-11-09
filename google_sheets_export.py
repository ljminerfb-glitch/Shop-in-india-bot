"""Best-effort SQLite workbook snapshot sync to a Google spreadsheet."""
import logging
import os
from pathlib import Path

log = logging.getLogger("shop_google_sheets")


def sync_workbook_to_google_sheets(workbook_path):
    """Mirror each workbook tab into the configured Google spreadsheet.

    The sync is a snapshot replacement, not an append-only event feed. Re-running
    it updates existing rows in place and therefore does not duplicate records.
    """
    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    credentials_path = Path(os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        str(Path("secrets") / "google_service_account.json"),
    ))
    if not spreadsheet_id:
        log.info("Google Sheets sync skipped: GOOGLE_SHEET_ID is not configured")
        return False
    if not credentials_path.is_file():
        log.warning("Google Sheets sync skipped: service-account JSON not found at %s",
                    credentials_path)
        return False

    try:
        import gspread
        client = gspread.service_account(filename=str(credentials_path))
        spreadsheet = client.open_by_key(spreadsheet_id)

        from openpyxl import load_workbook
        workbook = load_workbook(workbook_path, read_only=True, data_only=True)
        try:
            existing = {ws.title: ws for ws in spreadsheet.worksheets()}
            for source_sheet in workbook.worksheets:
                values = [
                    ["" if value is None else value for value in row]
                    for row in source_sheet.iter_rows(values_only=True)
                ]
                if not values:
                    values = [[""]]
                row_count = max(1, len(values))
                col_count = max(1, max(len(row) for row in values))
                target = existing.get(source_sheet.title)
                if target is None:
                    target = spreadsheet.add_worksheet(
                        title=source_sheet.title,
                        rows=max(100, row_count),
                        cols=max(26, col_count),
                    )
                else:
                    target.resize(rows=max(1, row_count), cols=max(1, col_count))
                target.clear()
                target.update(values, "A1", value_input_option="RAW")
            log.info("Google Sheets snapshot synced: %s", spreadsheet_id)
            return True
        finally:
            workbook.close()
    except Exception:
        # Cloud sync must never roll back or interrupt a committed SQLite transaction.
        log.exception("Google Sheets sync failed; local database and Excel backup are safe")
        return False
