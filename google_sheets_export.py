"""Best-effort SQLite workbook snapshot sync to a Google spreadsheet."""
import logging
import math
import os
from datetime import date, datetime, time
from pathlib import Path

log = logging.getLogger("shop_google_sheets")


def _cell_value(value):
    """Convert an Excel value to a Google Sheets ExtendedValue object."""
    if value is None:
        return {}
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isfinite(number):
            return {"userEnteredValue": {"numberValue": number}}
        return {"userEnteredValue": {"stringValue": str(value)}}
    if isinstance(value, (datetime, date, time)):
        return {"userEnteredValue": {"stringValue": value.isoformat()}}
    return {"userEnteredValue": {"stringValue": str(value)}}


def sync_workbook_to_google_sheets(workbook_path):
    """Mirror workbook tabs using one batched Sheets API write request.

    This replaces each tab's cell values (rather than appending rows), so repeated
    syncs do not create duplicate records. All tab updates and any required tab
    creation are sent in one spreadsheets.batchUpdate call to reduce quota use.
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
        log.warning(
            "Google Sheets sync skipped: service-account JSON not found at %s",
            credentials_path,
        )
        return False

    workbook = None
    try:
        import gspread
        from openpyxl import load_workbook

        client = gspread.service_account(filename=str(credentials_path))
        spreadsheet = client.open_by_key(spreadsheet_id)
        workbook = load_workbook(workbook_path, read_only=True, data_only=True)

        existing = {ws.title: ws for ws in spreadsheet.worksheets()}
        requests = []

        for source_sheet in workbook.worksheets:
            title = source_sheet.title
            values = [
                [value for value in row]
                for row in source_sheet.iter_rows(values_only=True)
            ]
            if not values:
                values = [[""]]

            row_count = max(1, len(values))
            col_count = max(1, max(len(row) for row in values))
            target = existing.get(title)

            if target is None:
                # Add the tab in the same batch as the cell updates.
                sheet_id = max(
                    [ws.id for ws in existing.values()] + [0]
                ) + 1
                while any(
                    req.get("addSheet", {}).get("properties", {}).get("sheetId") == sheet_id
                    for req in requests
                ):
                    sheet_id += 1
                requests.append({
                    "addSheet": {
                        "properties": {
                            "sheetId": sheet_id,
                            "title": title,
                            "gridProperties": {
                                "rowCount": max(100, row_count),
                                "columnCount": max(26, col_count),
                            },
                        }
                    }
                })
                grid_rows = max(100, row_count)
                grid_cols = max(26, col_count)
            else:
                sheet_id = target.id
                grid_rows = max(target.row_count, row_count)
                grid_cols = max(target.col_count, col_count)
                requests.append({
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {
                                "rowCount": grid_rows,
                                "columnCount": grid_cols,
                            },
                        },
                        "fields": "gridProperties.rowCount,gridProperties.columnCount",
                    }
                })

            requests.append({
                "updateCells": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "startColumnIndex": 0,
                        "endRowIndex": grid_rows,
                        "endColumnIndex": grid_cols,
                    },
                    "rows": [
                        {"values": [_cell_value(value) for value in row]}
                        for row in values
                    ],
                    "fields": "userEnteredValue",
                }
            })

        if requests:
            spreadsheet.batch_update({"requests": requests})

        log.info(
            "Google Sheets snapshot synced in one batched write: %s",
            spreadsheet_id,
        )
        return True
    except Exception:
        # Cloud sync must never roll back or interrupt a committed SQLite transaction.
        log.exception(
            "Google Sheets sync failed; local database and Excel backup are safe"
        )
        return False
    finally:
        if workbook is not None:
            workbook.close()
