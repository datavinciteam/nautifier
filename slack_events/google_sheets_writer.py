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
        logging.error(f"❌ Error writing to Google Sheets: {e}")
        return False

# Function to delete a row from Google Sheets
def delete_row_from_google_sheets(sheet_id, sheet_name, employee_name, from_date, to_date):
    """
    Deletes a row from the specified Google Sheet matching the employee, from_date, and to_date,
    but only if the leave status is 'UPCOMING'.

    Args:
        sheet_id (str): The Google Sheet ID.
        sheet_name (str): The name of the worksheet.
        employee_name (str): The name of the employee.
        from_date (str): The start date of the leave in DD/MM/YYYY format.
        to_date (str): The end date of the leave in DD/MM/YYYY format.

    Returns:
        tuple: (bool, str) - (True if a row was deleted, False otherwise; a message explaining the result).
    """
    try:
        client = authenticate_google_sheets()
        sheet = client.open_by_key(sheet_id).worksheet(sheet_name)

        # Fetch all rows
        all_rows = sheet.get_all_values()
        if not all_rows or len(all_rows) <= 1:  # No data or only header row
            logging.info("No data rows found in the sheet to delete.")
            return False, "No leave entries found in the sheet."

        # Identify the row to delete (match employee, from_date, to_date, and check status)
        row_to_delete = None
        for i, row in enumerate(all_rows):
            if i == 0:  # Skip header row
                continue
            if (len(row) >= 8 and  # Ensure row has enough columns (up to Status)
                row[1].strip() == employee_name.strip() and  # Employee (column B)
                row[3].strip() == from_date.strip() and      # From date (column D)
                row[4].strip() == to_date.strip() and        # To date (column E)
                row[7].strip().upper() == "UPCOMING"):       # Status (column H)
                row_to_delete = i + 1  # 1-based index for gspread
                break

        if row_to_delete is None:
            # Check if the row exists but is REDEEMED
            for i, row in enumerate(all_rows):
                if i == 0:  # Skip header row
                    continue
                if (len(row) >= 8 and
                    row[1].strip() == employee_name.strip() and
                    row[3].strip() == from_date.strip() and
                    row[4].strip() == to_date.strip() and
                    row[7].strip().upper() == "REDEEMED"):
                    logging.info(f"Cannot delete leave for {employee_name} from {from_date} to {to_date}: status is REDEEMED.")
                    return False, "Cannot cancel a past leave (status: REDEEMED)."
            logging.info(f"No matching UPCOMING leave found for {employee_name} from {from_date} to {to_date}.")
            return False, "No matching UPCOMING leave found to cancel."

        # Delete the row
        sheet.delete_rows(row_to_delete)
        logging.info(f"✅ Deleted row for {employee_name} from {from_date} to {to_date} at row {row_to_delete}.")
        return True, f"Leave for {from_date} to {to_date} has been cancelled."

    except Exception as e:
        logging.error(f"❌ Error deleting row from Google Sheets: {e}")
        return False, f"Error cancelling leave: {str(e)}"