import gspread
import logging
from google.auth import default
from datetime import datetime
from pytz import timezone

# Authenticate using Application Default Credentials (ADC)
def authenticate_google_sheets():
    creds, _ = default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
    client = gspread.authorize(creds)
    return client

# Function to write data to Google Sheets
def write_to_google_sheets(sheet_id, sheet_name, data):
    """
    Appends a row to the specified Google Sheet.

    Args:
        sheet_id (str): The Google Sheet ID.
        sheet_name (str): The name of the worksheet.
        data (list): The row data to append.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        client = authenticate_google_sheets()
        sheet = client.open_by_key(sheet_id).worksheet(sheet_name)
        sheet.append_row(data, value_input_option="USER_ENTERED")
        logging.info(f"✅ Successfully added row to Google Sheets: {data}")
        return True

    except Exception as e:
        logging.error(f"❌ Error writing to Google Sheets for sheet_id={sheet_id}, sheet_name={sheet_name}, data={data}: {e}")
        return False

# Function to delete rows from Google Sheets
def delete_row_from_google_sheets(sheet_id, sheet_name, employee_name, cancel_dates):
    """
    Deletes rows from the specified Google Sheet where the employee and date range exactly match
    any of the provided date ranges in cancel_dates, if the leave is UPCOMING or starts today, but not if REDEEMED.

    Args:
        sheet_id (str): The Google Sheet ID.
        sheet_name (str): The name of the worksheet.
        employee_name (str): The name of the employee.
        cancel_dates (list): List of dicts with from_date and to_date to cancel.

    Returns:
        tuple: (bool, list, str) - (True if at least one row was deleted, list of deleted date ranges, a message explaining the result).
    """
    try:
        client = authenticate_google_sheets()
        sheet = client.open_by_key(sheet_id).worksheet(sheet_name)

        # Fetch all rows
        all_rows = sheet.get_all_values()
        if not all_rows or len(all_rows) <= 1:  # No data or only header row
            logging.info(f"📝 No data rows found in sheet_id={sheet_id}, sheet_name={sheet_name} to delete.")
            return False, [], "No leave entries found in the sheet to cancel."

        # Get today's date in DD/MM/YYYY format
        IST = timezone("Asia/Kolkata")
        today_date = datetime.now(IST).strftime("%d/%m/%Y")
        today_date_dt = datetime.strptime(today_date, "%d/%m/%Y")

        # Identify rows to delete (exact match for any of the cancel_dates)
        rows_to_delete = []
        deleted_ranges = []
        for i, row in enumerate(all_rows):
            if i == 0:  # Skip header row
                continue
            if len(row) < 8:  # Ensure row has enough columns (up to Status)
                continue

            # Check if the row matches the employee
            if row[1].strip() != employee_name.strip():
                continue

            # Parse the row's date range
            row_from_date = row[3].strip()
            row_to_date = row[4].strip()
            try:
                row_from_dt = datetime.strptime(row_from_date, "%d/%m/%Y")
                row_to_dt = datetime.strptime(row_to_date, "%d/%m/%Y")
            except ValueError:
                logging.warning(f"📝 Invalid date format in row {i+1}: {row_from_date} to {row_to_date}. Skipping.")
                continue

            # Check for an exact match with any of the cancel_dates
            for cancel_range in cancel_dates:
                cancel_from_date = cancel_range["from_date"]
                cancel_to_date = cancel_range["to_date"]
                if (row_from_date == cancel_from_date and row_to_date == cancel_to_date):
                    # Check if the leave is cancellable (not REDEEMED and starts today or in the future)
                    if row[7].strip().upper() == "REDEEMED":
                        logging.info(f"📝 Cannot delete leave for {employee_name} from {row_from_date} to {row_to_date}: status is REDEEMED.")
                        return False, [], f"Cannot cancel leave from {row_from_date} to {row_to_date}: the leave has already been redeemed."
                    if row_from_dt < today_date_dt and row[7].strip().upper() != "UPCOMING":
                        logging.info(f"📝 Cannot delete leave for {employee_name} from {row_from_date} to {row_to_date}: leave is in the past.")
                        return False, [], f"Cannot cancel leave from {row_from_date} to {row_to_date}: the leave is in the past and not marked as UPCOMING."

                    rows_to_delete.append(i + 1)  # 1-based index for gspread
                    deleted_ranges.append({"from_date": row_from_date, "to_date": row_to_date})
                    break  # Stop checking other cancel ranges for this row
                else:
                    logging.info(f"📝 No exact match for {employee_name} from {cancel_from_date} to {cancel_to_date}. Row {i+1} has {row_from_date} to {row_to_date}.")

        if not rows_to_delete:
            logging.info(f"📝 No exactly matching cancellable leaves found for {employee_name} in sheet_id={sheet_id}, sheet_name={sheet_name}.")
            return False, [], f"No leave entries found for {employee_name} matching the requested dates to cancel. Please ensure the dates match exactly with a previously logged leave."

        # Delete rows in reverse order to avoid index shifting
        rows_to_delete.sort(reverse=True)
        for row_idx in rows_to_delete:
            sheet.delete_rows(row_idx)
            logging.info(f"✅ Deleted row for {employee_name} from {all_rows[row_idx-1][3]} to {all_rows[row_idx-1][4]} at row {row_idx} in sheet_id={sheet_id}, sheet_name={sheet_name}.")

        return True, deleted_ranges, f"Leave cancellation for {employee_name} processed."

    except Exception as e:
        logging.error(f"❌ Error deleting rows from Google Sheets for sheet_id={sheet_id}, sheet_name={sheet_name}, employee={employee_name}: {e}")
        return False, [], f"Error cancelling leave for {employee_name}: {str(e)}"