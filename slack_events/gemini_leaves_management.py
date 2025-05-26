import logging
import json
import requests
from slack_utils import send_threaded_reply, get_slack_user_name, fetch_thread_history
from secret_utils import get_secret
from datetime import datetime
import re
from pytz import timezone
from google_sheets_writer import write_to_google_sheets, delete_row_from_google_sheets

MODEL = "gemini-1.5-flash"
SHEET_ID = "1uhoqA6rPZJXWT-jiLLOaInodNc4FHxsxU_MNNRIGg1o"
LEAVES_SHEET_NAME = "Leaves"

# Define function declarations for Gemini API function calling
FUNCTION_DECLARATIONS = {
    "tools": [
        {
            "function_declarations": [
                {
                    "name": "process_leave_request",
                    "description": "Process a leave request and extract structured leave details. Returns a list of leave entries for non-continuous dates.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "leave_entries": {
                                "type": "array",
                                "description": "List of leave entries, each representing a continuous date range",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "leave_type": {"type": "string", "enum": ["casual", "sick", "half-day", "festive"], "description": "Type of leave"},
                                        "from_date": {"type": "string", "description": "Start date in DD/MM/YYYY format"},
                                        "to_date": {"type": "string", "description": "End date in DD/MM/YYYY format"},
                                        "num_days": {"type": "number", "description": "Number of leave days, excluding weekends, half-day counts as 0.5"},
                                        "reason_stated": {"type": "string", "description": "Reason for the leave, if provided"}
                                    },
                                    "required": ["leave_type", "from_date", "to_date", "num_days"]
                                }
                            },
                            "reply": {"type": "string", "description": "Friendly reply message acknowledging the leave"}
                        },
                        "required": ["leave_entries", "reply"]
                    }
                },
                {
                    "name": "cancel_leave_request",
                    "description": "Cancel a previously logged leave request.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "from_date": {"type": "string", "description": "Start date of the leave to cancel in DD/MM/YYYY format"},
                            "to_date": {"type": "string", "description": "End date of the leave to cancel in DD/MM/YYYY format"},
                            "reply": {"type": "string", "description": "Friendly reply message acknowledging the cancellation"}
                        },
                        "required": ["from_date", "to_date", "reply"]
                    }
                }
            ]
        }
    ]
}

SYSTEM_INSTRUCTION = { 
    "parts": [
        {
            "text": """You are Nautifier, a Slack bot in the leaves channel where people announce their leaves to avoid manually filling leave forms.
Messages could be for sick leave, casual leave, festive leaves, half days, etc. Users may post in threads, where a tentative leave request (e.g., "might be on leave") can be confirmed later (e.g., "confirming leaves"). Users may also cancel leaves (e.g., "cancel leave for 7th").

Your job is to:
1. Detect if the thread is a leave request or a cancellation request.
2. For leave requests, call the `process_leave_request` function to extract:
   - **leave_entries**: A list of leave entries, where each entry represents a continuous date range.
     - Group dates into continuous ranges (e.g., 12th and 13th become one entry, but 10th and 12th are separate).
     - For each entry:
       - **leave_type**: (casual, sick, half-day, festive). If someone is sick but requests a half-day, mark it as *sick*. Default to *casual* if unclear.
       - **from_date & to_date**: Extract leave dates in `DD/MM/YYYY` format for each continuous range. If no dates are mentioned, assume today's date. If the year is not specified, assume the current year or the next year if the date has passed.
       - **num_days**: Calculate the number of leave days for each entry, excluding weekends (Saturday and Sunday). Each half-day counts as 0.5 days.
       - **reason_stated**: Reason stated by the user for the leave, if provided (same for all entries).
   - **reply**: Generate a professional message acknowledging the leave (e.g., "Hi [User], your leave request has been noted. Thanks!").
3. For cancellation requests, call the `cancel_leave_request` function to extract:
   - **from_date & to_date**: The dates of the leave to cancel in `DD/MM/YYYY` format.
   - **reply**: Generate a friendly message acknowledging the cancellation.

### Date Grouping Rules:
- Group consecutive dates into a single entry (e.g., 12th and 13th become one entry: 12th to 13th).
- Non-consecutive dates should be separate entries (e.g., 10th, 12th, 13th, 18th become three entries: 10th, 12th-13th, 18th).
- For a request like "2 days leave on 30th May and 16th June," create two entries: one for 30th May (1 day) and one for 16th June (1 day).

### Thread Handling:
- Treat the thread as a brunch. Prioritize the latest message for determining intent (e.g., confirmation or cancellation).
- If a user posts a tentative leave (e.g., 'might be on leave on 6th May') and later confirms (e.g., 'confirming leaves'), interpret it as a confirmed leave request.
- Combine all thread messages to determine the intent, but give higher weight to the most recent message.

If you cannot determine the intent or details, respond with a detailed reason why the request couldn't be processed, such as:
- "I couldn't find any specific dates in your message."
- "The leave type is unclear. Please specify if it's casual, sick, half-day, or festive."
- "The message is ambiguous. Please confirm if this is a leave request or cancellation."
Always suggest how the user can improve their request, e.g., "Please include specific dates in DD/MM/YYYY format and clarify the leave type."
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

    Args:
        date_str (str): Date in DD/MM/YYYY format.

    Returns:
        bool: True if valid, False otherwise.
    """
    try:
        datetime.strptime(date_str, "%d/%m/%Y")
        return True
    except ValueError:
        return False

def get_gemini_response(prompt):
    """
    Fetches a response from Gemini AI using function calling.
    Returns a tuple: (status, data, error_message)
    - status: "success", "failure", or "cancel"
    - data: leave_entries and reply for success, or None for failure/cancel
    - error_message: detailed reason for failure, if applicable
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
        logging.info(f"📩 Full Gemini API Response: {json.dumps(data, indent=2)}")

        candidates = data.get("candidates", [])
        if not candidates:
            return "failure", None, "Incomplete response from the AI service. Please try again or tag Prateek for assistance."

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if not parts:
            return "failure", None, "Incomplete response from the AI service. Please try again or tag Prateek for assistance."

        for part in parts:
            if "functionCall" in part:
                function_call = part["functionCall"]
                function_name = function_call.get("name")
                args = function_call.get("args", {})
                if function_name == "process_leave_request":
                    leave_entries = args.get("leave_entries", [])
                    if not leave_entries:
                        return "failure", None, "No leave details were provided. Please include specific dates in DD/MM/YYYY format and clarify the leave type (e.g., casual, sick, half-day, festive)."
                    return "success", [args.get("reply"), leave_entries], None
                elif function_name == "cancel_leave_request":
                    return "cancel", [args.get("reply"), {
                        "from_date": args.get("from_date"),
                        "to_date": args.get("to_date")
                    }], None

        response_text = parts[-1].get("text", "").strip()
        if "couldn't find any specific dates" in response_text.lower():
            return "failure", None, f"{response_text} Please include specific dates in DD/MM/YYYY format (e.g., 30/05/2025)."
        elif "leave type is unclear" in response_text.lower():
            return "failure", None, f"{response_text} Please specify the leave type (e.g., casual, sick, half-day, festive)."
        elif "message is ambiguous" in response_text.lower():
            return "failure", None, f"{response_text} Please use clear language to indicate a leave request (e.g., 'I need a sick leave on 30/05/2025') or cancellation (e.g., 'cancel leave for 30/05/2025')."
        else:
            return "failure", None, f"{response_text} Please try again with clear, unambiguous language, or tag Prateek for assistance."

    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error calling Gemini API: {e}")
        return "failure", None, "Failed to process the request due to an issue with the AI service. Please try again or tag Prateek for assistance."

def handle_leaves_management_event(event):
    """
    Handles leave announcements and cancellations in Slack threads, logs to Google Sheets, and deletes rows for cancellations.
    """
    try:
        user_id = event.get("user", "")
        channel = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        mentioned_text = event.get("text", "")

        IST = timezone("Asia/Kolkata")
        today_date = datetime.now(IST).strftime("%d/%m/%Y")

        slack_user_name = get_slack_user_name(user_id)
        
        # Build thread context
        thread_history = fetch_thread_history(channel, thread_ts, exclude_ts=None)
        thread_history.append(f"{slack_user_name}: {mentioned_text}")
        
        # Add context to the prompt to indicate thread messages
        thread_context = "\n".join([f"Message {i+1}: {msg}" for i, msg in enumerate(thread_history)])
        prompt = f"Today's date is {today_date}. Use this when required.\nThe following messages are part of a Slack thread:\n{thread_context}"
        status, data, error_message = get_gemini_response(prompt)

        if status == "failure":
            send_threaded_reply(channel, thread_ts, error_message)
            return json.dumps({"status": "processing_failed"}), 200

        # Handle leave request
        if status == "success":
            reply_message = data[0].replace("[User]", slack_user_name)  # Dynamically insert user name
            leave_entries = data[1]
            slack_message = f"{reply_message}\n\n**Leave Details:**\n"
            all_writes_successful = True

            total_days = sum(entry["num_days"] for entry in leave_entries)
            slack_message += f"Total Leave Duration: {total_days} day{'s' if total_days != 1 else ''}\n\n"

            for leave in leave_entries:
                if not (is_valid_date(leave["from_date"]) and is_valid_date(leave["to_date"])):
                    send_threaded_reply(channel, thread_ts, "Invalid date format in leave request. Please use DD/MM/YYYY (e.g., 30/05/2025) and try again, or tag Prateek for assistance.")
                    return json.dumps({"status": "failed"}), 400

                # Attempt to write to Google Sheets
                success = write_to_google_sheets(SHEET_ID, LEAVES_SHEET_NAME, [
                    datetime.now(IST).strftime("%d/%m/%Y %H:%M:%S"),
                    slack_user_name,
                    leave["leave_type"],
                    leave["from_date"],
                    leave["to_date"],
                    leave["num_days"],
                    leave.get("reason_stated", "Not provided")
                ])

                if success:
                    date_range = leave["from_date"] if leave["from_date"] == leave["to_date"] else f"{leave['from_date']} to {leave['to_date']}"
                    slack_message += (
                        f"• **Type:** {leave['leave_type'].capitalize()}\n"
                        f"• **Duration:** {leave['num_days']} day{'s' if leave['num_days'] != 1 else ''}\n"
                        f"• **Dates:** {date_range}\n"
                        f"• **Reason:** {leave.get('reason_stated', 'Not provided')}\n\n"
                    )
                else:
                    all_writes_successful = False
                    logging.error(f"❌ Failed to write leave for {slack_user_name} to Google Sheets: {leave}")

            if all_writes_successful:
                slack_message += "_To cancel, reply in this thread with 'cancel leave for <date>' or a date range (e.g., 'cancel leave for 30/05/2025 to 31/05/2025')._"
                send_threaded_reply(channel, thread_ts, slack_message)
                return json.dumps({"status": "logged"}), 200
            else:
                send_threaded_reply(channel, thread_ts, f"Hi {slack_user_name}, there was an error logging your leave to Google Sheets. Please try again or tag Prateek for assistance.")
                return json.dumps({"status": "failed"}), 500

        # Handle cancellation
        else:  # status == "cancel"
            default_reply = data[0].replace("[User]", slack_user_name)
            cancel_details = data[1]
            from_date = cancel_details["from_date"]
            to_date = cancel_details["to_date"]

            if not (is_valid_date(from_date) and is_valid_date(to_date)):
                send_threaded_reply(channel, thread_ts, "Invalid date format in cancellation request. Please use DD/MM/YYYY (e.g., 30/05/2025) and try again, or tag Prateek for assistance.")
                return json.dumps({"status": "failed"}), 400

            # Delete the row(s) from Google Sheets
            success, message = delete_row_from_google_sheets(SHEET_ID, LEAVES_SHEET_NAME, slack_user_name, from_date, to_date)
            logging.info(f"📝 Cancellation attempt for {slack_user_name} from {from_date} to {to_date}: Success={success}, Message={message}")

            # Use the AI-generated reply only if deletion succeeds; otherwise, use the message from delete_row_from_google_sheets
            final_reply = default_reply if success else message
            send_threaded_reply(channel, thread_ts, final_reply)
            return json.dumps({"status": "cancelled" if success else "failed"}), 200

    except Exception as e:
        logging.error(f"❌ Error in handle_leaves_management_event: {e}")
        send_threaded_reply(channel, thread_ts, f"Hi {slack_user_name}, an unexpected error occurred while processing your request. Please try again or tag Prateek for assistance.")
        return json.dumps({"error": str(e)}), 500