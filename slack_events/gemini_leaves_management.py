import logging
import json
import requests
from slack_utils import send_threaded_reply, get_slack_user_name, fetch_thread_history
from secret_utils import get_secret
from datetime import datetime
from pytz import timezone
from google_sheets_writer import write_to_google_sheets, delete_row_from_google_sheets

MODEL = "gemini-2.0-flash-lite"
SHEET_ID = "1uhoqA6rPZJXWT-jiLLOaInodNc4FHxsxU_MNNRIGg1o"
LEAVES_SHEET_NAME = "Leaves"

# Define function declarations for Gemini API function calling
FUNCTION_DECLARATIONS = {
    "tools": [
        {
            "function_declarations": [
                {
                    "name": "process_leave_request",
                    "description": "Extract leave request details from a Slack message thread.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "leave_entries": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "leave_type": {"type": "string", "enum": ["casual", "sick", "half-day", "festive"], "description": "Type of leave"},
                                        "from_date": {"type": "string", "description": "Start date in DD/MM/YYYY format"},
                                        "to_date": {"type": "string", "description": "End date in DD/MM/YYYY format"},
                                        "num_days": {"type": "number", "description": "Number of leave days, excluding weekends, half-day counts as 0.5"},
                                        "reason": {"type": "string", "description": "Reason for the leave, if provided"}
                                    },
                                    "required": ["leave_type", "from_date", "to_date", "num_days"]
                                }
                            }
                        },
                        "required": ["leave_entries"]
                    }
                },
                {
                    "name": "cancel_leave_request",
                    "description": "Extract cancellation request details from a Slack message thread.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "from_date": {"type": "string", "description": "Start date to cancel in DD/MM/YYYY format"},
                            "to_date": {"type": "string", "description": "End date to cancel in DD/MM/YYYY format"}
                        },
                        "required": ["from_date", "to_date"]
                    }
                }
            ]
        }
    ]
}

SYSTEM_INSTRUCTION = {
    "parts": [
        {
            "text": """You are Nautifier, a Slack bot that helps users log leaves in a channel. Users post messages in threads, like "I'm sick today", "casual leave from 10/06/2025 to 12/06/2025", or "cancel leave for 10/06/2025". A user might post a tentative leave (e.g., "might be on leave on 10th June") and later confirm it (e.g., "confirming leaves") in the same thread.

Your job is to:
1. Detect if the thread is a leave request or a cancellation request.
2. For leave requests, call the `process_leave_request` function to extract:
   - **leave_entries**: A list of leave entries, where each entry represents a continuous date range.
     - Group consecutive dates into a single entry (e.g., 12th to 13th).
     - Non-consecutive dates should be separate entries (e.g., 10th, 12th-13th, 18th become three entries).
     - For each entry:
       - **leave_type**: Identify as "casual", "sick", "half-day", or "festive". Use these rules:
         - "sick" if the user mentions "sick", "ill", "unwell", or "not feeling well" (e.g., "I'm sick today", "not feeling well on 10th").
         - "half-day" if the user explicitly mentions "half day" or "half-day" (e.g., "half day on 10th").
         - "festive" if the user mentions "festive" or a festival (e.g., "festive leave for Diwali").
         - "casual" as the default if none of the above apply (e.g., "leave on 10th", "out on 12th").
         - If "sick" and "half-day" are both mentioned (e.g., "I'm sick, need a half day"), classify as "sick".
       - **from_date & to_date**: Extract dates in `DD/MM/YYYY` format. If no dates are mentioned, assume today's date. If the year is not specified, assume the current year (or next year if the date has passed).
       - **num_days**: Calculate the number of leave days, excluding weekends (Saturday and Sunday). Each half-day counts as 0.5 days.
       - **reason**: Extract the reason if provided (e.g., "for a family event", "because I'm unwell").
3. For cancellation requests, call the `cancel_leave_request` function to extract:
   - **from_date & to_date**: The exact date range to cancel in `DD/MM/YYYY` format. Treat the cancellation request as a single range:
     - If the user specifies a range (e.g., "cancel leave for 01/06/2025 to 21/06/2025"), use that exact range.
     - If the user specifies multiple dates (e.g., "cancel leave for 01/06/2025, 02/06/2025, 04/06/2025"), treat the earliest and latest dates as the range.
     - If the user specifies a single date (e.g., "cancel leave for 01/06/2025"), set from_date and to_date to the same date.

### Thread Handling:
- Treat the thread as a conversation. Prioritize the latest message for determining intent (e.g., confirmation or cancellation).
- If a user posts a tentative leave (e.g., "might be on leave on 10th June") and later confirms (e.g., "confirming leaves"), interpret it as a confirmed leave request.
- Combine all thread messages to determine the details, but give higher weight to the most recent message.

### Examples:
- "I'm sick today" → leave_type: "sick", from_date: today's date, to_date: today's date, num_days: 1
- "leave on 10th June" → leave_type: "casual", from_date: "10/06/YYYY", to_date: "10/06/YYYY", num_days: 1
- "half day on 10th June because of a doctor visit" → leave_type: "half-day", from_date: "10/06/YYYY", to_date: "10/06/YYYY", num_days: 0.5, reason: "doctor visit"
- "sick half day on 10th June" → leave_type: "sick", from_date: "10/06/YYYY", to_date: "10/06/YYYY", num_days: 0.5
- "cancel leave for 10th June" → from_date: "10/06/YYYY", to_date: "10/06/YYYY"

If you cannot determine the intent or details, respond with a short error message like:
- "I couldn't find any dates in your message."
- "I couldn't determine the leave type. Please specify if it's casual, sick, half-day, or festive."
- "Please clarify if this is a leave request or cancellation."
"""
        }
    ]
}

GENERATION_CONFIG = {
    "temperature": 1.0,
    "topP": 0.95,
    "topK": 64,
    "maxOutputTokens": 512,
    "responseMimeType": "text/plain",
}

def is_valid_date(date_str):
    """
    Validates if a date string is in DD/MM/YYYY format and is a valid date.
    """
    try:
        datetime.strptime(date_str, "%d/%m/%Y")
        return True
    except ValueError:
        return False

def validate_leave_entry(entry):
    """
    Validates a leave entry to ensure all required fields are present and valid.
    Returns a tuple: (is_valid, error_message)
    """
    required_fields = ["leave_type", "from_date", "to_date", "num_days"]
    for field in required_fields:
        if field not in entry:
            return False, f"Missing required field: {field}"
    if entry["leave_type"] not in ["casual", "sick", "half-day", "festive"]:
        return False, f"Invalid leave type: {entry['leave_type']}"
    if not (is_valid_date(entry["from_date"]) and is_valid_date(entry["to_date"])):
        return False, "Invalid date format. Please use DD/MM/YYYY (e.g., 30/05/2025)."
    if not isinstance(entry["num_days"], (int, float)) or entry["num_days"] <= 0:
        return False, f"Invalid number of days: {entry['num_days']}"
    return True, None

def get_gemini_response(prompt):
    """
    Fetches a response from Gemini AI using function calling.
    Returns a tuple: (status, data, error_message)
    - status: "success", "cancel", or "failure"
    - data: leave_entries for success, cancellation details for cancel, or None for failure
    - error_message: reason for failure, if applicable
    """
    try:
        api_key = get_secret("GEMINI_API_KEY")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={api_key}"

        payload = {
            "contents": [{"role": "USER", "parts": [{"text": prompt}]}],
            "system_instruction": SYSTEM_INSTRUCTION,
            "generation_config": GENERATION_CONFIG,
            "tools": FUNCTION_DECLARATIONS["tools"]
        }
        headers = {"Content-Type": "application/json"}

        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()

        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return "failure", None, "I couldn't process your request due to an incomplete response from the AI service. Please try again or tag HR for help."

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if not parts:
            return "failure", None, "I couldn't process your request due to an incomplete response from the AI service. Please try again or tag HR for help."

        for part in parts:
            if "functionCall" in part:
                function_call = part["functionCall"]
                function_name = function_call.get("name")
                args = function_call.get("args", {})
                if function_name == "process_leave_request":
                    leave_entries = args.get("leave_entries", [])
                    if not leave_entries:
                        return "failure", None, "I couldn't find any leave details in your message. Please include dates in DD/MM/YYYY format (e.g., 30/05/2025)."
                    # Validate each leave entry
                    for entry in leave_entries:
                        is_valid, error = validate_leave_entry(entry)
                        if not is_valid:
                            return "failure", None, f"Invalid leave entry: {error}"
                    return "success", leave_entries, None
                elif function_name == "cancel_leave_request":
                    from_date = args.get("from_date")
                    to_date = args.get("to_date")
                    if not (from_date and to_date):
                        return "failure", None, "I couldn't find any dates to cancel. Please specify dates in DD/MM/YYYY format (e.g., 30/05/2025)."
                    if not (is_valid_date(from_date) and is_valid_date(to_date)):
                        return "failure", None, "Invalid date format for cancellation. Please use DD/MM/YYYY (e.g., 30/05/2025)."
                    return "cancel", {"from_date": from_date, "to_date": to_date}, None

        response_text = parts[-1].get("text", "").strip()
        return "failure", None, response_text

    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error calling Gemini API: {e}")
        return "failure", None, "I couldn't reach the AI service. Please try again or tag HR for help."

def handle_leaves_management_event(event):
    """
    Handles leave announcements and cancellations in Slack threads, logs to Google Sheets, and deletes rows for cancellations.
    """
    try:
        user_id = event.get("user", "")
        channel = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        message = event.get("text", "")
        if not (user_id and channel and thread_ts and message):
            logging.error(f"❌ Missing required event fields: {event}")
            return json.dumps({"status": "failed", "error": "Missing required event fields"}), 400

        IST = timezone("Asia/Kolkata")
        today_date = datetime.now(IST).strftime("%d/%m/%Y")
        slack_user_name = get_slack_user_name(user_id)

        # Build thread context
        thread_history = fetch_thread_history(channel, thread_ts, exclude_ts=None)
        thread_history.append(f"{slack_user_name}: {message}")
        thread_context = "\n".join([f"Message {i+1}: {msg}" for i, msg in enumerate(thread_history)])
        prompt = f"Today's date is {today_date}. Use this when required.\nThe following messages are part of a Slack thread:\n{thread_context}"

        status, data, error_message = get_gemini_response(prompt)

        if status == "failure":
            send_threaded_reply(channel, thread_ts, f"Hey {slack_user_name}, {error_message}")
            return json.dumps({"status": "processing_failed"}), 200

        # Handle leave request
        if status == "success":
            leave_entries = data
            total_days = sum(entry["num_days"] for entry in leave_entries)
            slack_message = f"Hey {slack_user_name}, I've noted your leave request! 🎉\n\n"

            for entry in leave_entries:
                success = write_to_google_sheets(SHEET_ID, LEAVES_SHEET_NAME, [
                    datetime.now(IST).strftime("%d/%m/%Y %H:%M:%S"),
                    slack_user_name,
                    entry["leave_type"],
                    entry["from_date"],
                    entry["to_date"],
                    entry["num_days"],
                    entry.get("reason", "Not provided")
                ])

                if not success:
                    send_threaded_reply(channel, thread_ts, f"Hey {slack_user_name}, I couldn't log your leave to Google Sheets. Please try again or tag HR for help.")
                    return json.dumps({"status": "failed"}), 500

                date_range = entry["from_date"] if entry["from_date"] == entry["to_date"] else f"{entry['from_date']} to {entry['to_date']}"
                slack_message += (
                    f"- {entry['leave_type'].capitalize()} leave for {date_range}\n"
                    f"- {entry['num_days']} day{'s' if entry['num_days'] != 1 else ''}\n"
                    f"- Reason: {entry.get('reason', 'Not provided')}\n\n"
                )

            slack_message += f"Total: {total_days} day{'s' if total_days != 1 else ''}"
            send_threaded_reply(channel, thread_ts, slack_message)
            return json.dumps({"status": "logged"}), 200

        # Handle cancellation
        else:  # status == "cancel"
            from_date = data["from_date"]
            to_date = data["to_date"]
            success, message = delete_row_from_google_sheets(SHEET_ID, LEAVES_SHEET_NAME, slack_user_name, from_date, to_date)
            if success:
                date_range = from_date if from_date == to_date else f"{from_date} to {to_date}"
                slack_message = f"Hey {slack_user_name}, I've cancelled your leave for {date_range}. All set now! ✅"
            else:
                slack_message = f"Hey {slack_user_name}, {message}"

            send_threaded_reply(channel, thread_ts, slack_message)
            return json.dumps({"status": "cancelled" if success else "failed"}), 200

    except Exception as e:
        logging.error(f"❌ Error in handle_leaves_management_event: {e}")
        send_threaded_reply(channel, thread_ts, f"Hey {slack_user_name}, something went wrong while processing your request. Please try again or tag HR for help.")
        return json.dumps({"error": str(e)}), 500
