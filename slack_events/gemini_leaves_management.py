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
                    "description": "Process a leave request and extract structured leave details.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "leave_type": {"type": "string", "enum": ["casual", "sick", "half-day", "festive"], "description": "Type of leave"},
                            "from_date": {"type": "string", "description": "Start date in DD/MM/YYYY format"},
                            "to_date": {"type": "string", "description": "End date in DD/MM/YYYY format"},
                            "num_days": {"type": "number", "description": "Number of leave days, excluding weekends, half-day counts as 0.5"},
                            "reason_stated": {"type": "string", "description": "Reason for the leave, if provided"},
                            "reply": {"type": "string", "description": "Professional reply message acknowledging the leave"}
                        },
                        "required": ["leave_type", "from_date", "to_date", "num_days", "reply"]
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
                            "reply": {"type": "string", "description": "Professional reply message acknowledging the cancellation"}
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
   - **leave_type**: (casual, sick, half-day, festive). If someone is sick but requests a half-day, mark it as *sick*. Default to *casual* if unclear.
   - **from_date & to_date**: Extract leave dates in `DD/MM/YYYY` format. If no dates are mentioned, assume today's date. If the year is not specified, assume the current year or the next year if the date has passed.
   - **num_days**: Calculate the number of leave days, excluding weekends (Saturday and Sunday). Each half-day counts as 0.5 days.
   - **reply**: Generate a professional message acknowledging the leave.
   - **reason_stated**: Reason stated by the user for the leave, if provided.
3. For cancellation requests, call the `cancel_leave_request` function to extract:
   - **from_date & to_date**: The dates of the leave to cancel in `DD/MM/YYYY` format.
   - **reply**: Generate a professional message acknowledging the cancellation.

### Thread Handling:
- Treat the thread as a conversation. If a user posts a tentative leave (e.g., 'might be on leave on 6th May') and later confirms (e.g., 'confirming leaves'), interpret it as a confirmed leave request.
- Combine all thread messages to determine the intent (leave request or cancellation).

If you cannot determine the intent or details, respond with: 'I cannot determine if a leave form fill-up is required.'"""
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

def get_gemini_response(prompt):
    """
    Fetches a response from Gemini AI using function calling.
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
            return ["I cannot determine if a leave form fill-up is required."]

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if not parts:
            return ["I cannot determine if a leave form fill-up is required."]

        for part in parts:
            if "functionCall" in part:
                function_call = part["functionCall"]
                function_name = function_call.get("name")
                args = function_call.get("args", {})
                if function_name == "process_leave_request":
                    return [args.get("reply"), {
                        "leave_type": args.get("leave_type"),
                        "from_date": args.get("from_date"),
                        "to_date": args.get("to_date"),
                        "num_days": args.get("num_days"),
                        "reason_stated": args.get("reason_stated", "Not provided")
                    }]
                elif function_name == "cancel_leave_request":
                    return ["cancel", args.get("reply"), {
                        "from_date": args.get("from_date"),
                        "to_date": args.get("to_date")
                    }]

        response_text = parts[-1].get("text", "").strip()
        if "I cannot determine if a leave form fill-up is required" in response_text:
            return ["I cannot determine if a leave form fill-up is required."]

        logging.warning("⚠️ No function call or valid response found.")
        return ["I cannot determine if a leave form fill-up is required."]

    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error calling Gemini API: {e}")
        return ["I cannot determine if a leave form fill-up is required."]

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
        ai_response = get_gemini_response(prompt)

        if not ai_response or ai_response[0] == "I cannot determine if a leave form fill-up is required.":
            send_threaded_reply(channel, thread_ts, "I cannot determine if a leave form fill-up is required.")
            return json.dumps({"status": "processing_failed"}), 200

        # Handle leave request
        if ai_response[0] != "cancel":
            reply_message = ai_response[0]
            leave_entries = ai_response[1:]

            slack_message = f"{reply_message}\n\n ***Leave Details:***\n"
            for leave in leave_entries:
                slack_message += (
                    f"Type: {leave['leave_type'].capitalize()}\n"
                    f"Duration: {leave['num_days']} days\n"
                    f"Dates: {leave['from_date']} to {leave['to_date']}\n"
                    f"Reason: {leave.get('reason_stated', 'Not provided')}\n"
                    f"---\n\n"
                )

                write_to_google_sheets(SHEET_ID, LEAVES_SHEET_NAME, [
                    datetime.now(IST).strftime("%d/%m/%Y %H:%M:%S"),
                    slack_user_name,
                    leave["leave_type"],
                    leave["from_date"],
                    leave["to_date"],
                    leave["num_days"],
                    leave.get("reason_stated", "Not provided")
                ])

            send_threaded_reply(channel, thread_ts, slack_message)
            return json.dumps({"status": "logged"}), 200

        # Handle cancellation
        else:
            default_reply = ai_response[1]  # AI-generated reply, e.g., "Leave for 07/05/2025 has been cancelled."
            cancel_details = ai_response[2]
            from_date = cancel_details["from_date"]
            to_date = cancel_details["to_date"]

            # Delete the row from Google Sheets (only if UPCOMING)
            success, message = delete_row_from_google_sheets(SHEET_ID, LEAVES_SHEET_NAME, slack_user_name, from_date, to_date)
            logging.info(f"Cancellation attempt for {slack_user_name} from {from_date} to {to_date}: Success={success}, Message={message}")

            # Use the AI-generated reply only if deletion succeeds; otherwise, use the message from delete_row_from_google_sheets
            final_reply = default_reply if success else message

            send_threaded_reply(channel, thread_ts, final_reply)
            return json.dumps({"status": "cancelled" if success else "failed"}), 200

    except Exception as e:
        logging.error(f"❌ Error in handle_leaves_management_event: {e}")
        return json.dumps({"error": str(e)}), 500