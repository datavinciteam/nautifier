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

    Returns:
    - bool: True if successful, False otherwise.
    """
    try:
        client = authenticate_google_sheets()
        sheet = client.open_by_key(sheet_id).worksheet(sheet_name)
        sheet.append_row(data, value_input_option="USER_ENTERED") #changed from RAW to USER_ENTERED fro date formatting
        logging.info(f"✅ Successfully added row to Google Sheets: {data}")
        return True

    except Exception as e:
        logging.error(f"❌ Error writing to Google Sheets: {e}")
        return False
