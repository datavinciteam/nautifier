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

# Function to delete a row from Google Sheets
def delete_row_from_google_sheets(sheet_id, sheet_name, employee_name, from_date, to_date):
    """
    Deletes a row from the specified Google Sheet matching the employee, from_date, and to_date,
    if the leave is UPCOMING or starts today, but not if REDEEMED.

    Args:
        sheet_id (str): The Google Sheet ID.
        sheet_name (str): The name of the worksheet.
        employee_name (str): The name of the employee.
        from_date (str): The start date of the leave in DD/MM/YYYY format.
        to_date (str): The end date of the leave in DD/MM/YYYY format.

    Returns:
        tuple: (bool, str) - (True if a row was deleted, False otherwise; a | message explaining the result).
    """
    try:
        client = authenticate_google_sheets()
        sheet = client.open_by_key(sheet_id).worksheet(sheet_name)

        # Fetch all rows
        all_rows = sheet.get_all_values()
        if not all_rows or len(all_rows) <= 1:  # No data or only header row
            logging.info(f"📝 No data rows found in sheet_id={sheet_id}, sheet_name={sheet_name} to delete.")
            return False, "No leave entries found in the sheet."

        # Get today's date in DD/MM/YYYY format
        IST = timezone("Asia/Kolkata")
        today_date = datetime.now(IST).strftime("%d/%m/%Y")

        # Convert from_date to datetime for comparison
        from_date_dt = datetime.strptime(from_date, "%d/%m/%Y")
        today_date_dt = datetime.strptime(today_date, "%d/%m/%Y")

        # Identify the row to delete
        row_to_delete = None
        for i, row in enumerate(all_rows):
            if i == 0:  # Skip header row
                continue
            if (len(row) >= 8 and  # Ensure row has enough columns (up to Status)
                row[1].strip() == employee_name.strip() and  # Employee (column B)
                row[3].strip() == from_date.strip() and      # From date (column D)
                row[4].strip() == to_date.strip()):          # To date (column E)
                if row[7].strip().upper() == "REDEEMED":
                    logging.info(f"📝 Cannot delete leave for {employee_name} from {from_date} to {to_date}: status is REDEEMED.")
                    return False, "Cannot cancel a past leave (status: REDEEMED)."
                if from_date_dt >= today_date_dt or row[7].strip().upper() == "UPCOMING":
                    row_to_delete = i + 1  # 1-based index for gspread
                    break

        if row_to_delete is None:
            logging.info(f"📝 No matching cancellable leave found for {employee_name} from {from_date} to {to_date} in sheet_id={sheet_id}, sheet_name={sheet_name}.")
            return False, "No matching leave found to cancel (must be today or upcoming, not redeemed)."

        # Delete the row
        sheet.delete_rows(row_to_delete)
        logging.info(f"✅ Deleted row for {employee_name} from {from_date} to {to_date} at row {row_to_delete} in sheet_id={sheet_id}, sheet_name={sheet_name}.")
        return True, f"Leave for {from_date} to {to_date} has been cancelled."

    except Exception as e:
        logging.error(f"❌ Error deleting row from Google Sheets for sheet_id={sheet_id}, sheet_name={sheet_name}, employee={employee_name}, from_date={from_date}, to_date={to_date}: {e}")
        return False, f"Error cancelling leave: {str(e)}"