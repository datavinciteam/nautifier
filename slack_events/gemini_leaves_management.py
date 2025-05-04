import logging
import json
import requests
from slack_utils import send_threaded_reply, get_slack_user_name, fetch_thread_history
from secret_utils import get_secret
from datetime import datetime
import re
from pytz import timezone
from google_sheets_writer import write_to_google_sheets

MODEL = "gemini-1.5-flash"
SHEET_ID = "1uhoqA6rPZJXWT-jiLLOaInodNc4FHxsxU_MNNRIGg1o"  # Replace with actual Sheet ID
LEAVES_SHEET_NAME = "Leaves"  # Name of the tab inside Google Sheets

SYSTEM_INSTRUCTION = { 
    "parts": [
        {
            "text": """You are Nautifier, a Slack bot in the leaves channel where people announce their leaves to avoid manually filling leave forms.
Messages could be for sick leave, casual leave, festive leaves, half days, etc. Users may post in threads, where a tentative leave request (e.g., "might be on leave") can be confirmed later (e.g., "confirming leaves").

Your job is to extract leave details from the entire thread:
1. **leave_type**: (casual, sick, half-day, festive). If someone is sick but requests a half-day, mark it as *sick*. Default to *casual* if unclear.
2. **from_date & to_date**: Extract leave dates in `DD/MM/YYYY` format. If no dates are mentioned, assume today's date. If the year is not specified, assume the current year or the next year if the date has passed.
3. **num_days**: Calculate the number of leave days, excluding weekends (Saturday and Sunday). Each half-day counts as 0.5 days.
4. **reply**: Generate an **appropriate, professional message** acknowledging the leave.
5. **reason_stated**: Reason stated by the user for the leave, if provided.

### Thread Handling:
- Treat the thread as a conversation. If a user posts a tentative leave (e.g., "might be on leave on 6th May") and later confirms (e.g., "confirming leaves"), interpret it as a confirmed leave request.
- Combine all thread messages to determine the leave details.

### Output Format:
- Return an **array of JSON objects**.
- The first element is `"reply"` followed by **structured leave details**.
- Example output:
```json
[
    "Noted. Wishing you a speedy recovery!",
    { "leave_type": "sick", "from_date": "10/02/2025", "to_date": "10/02/2025", "num_days": 1, "reason_stated": "Feeling nauseous" }
]
If you cannot determine leave details from the thread, respond: 'I cannot determine if a leave form fill-up is required.' """
        }
    ]
}

GENERATION_CONFIG = {
    "temperature": 1.20,
    "topP": 0.95,
    "topK": 64,
    "maxOutputTokens": 512,
    "responseMimeType": "text/plain",
}

def get_gemini_response(prompt):
    """
    Fetches a response from Gemini AI and extracts structured JSON from the response text.
    """
    try:
        api_key = get_secret("GEMINI_API_KEY")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={api_key}"

        payload = {
            "contents": [{"role": "USER", "parts": [{"text": prompt}]}],
            "system_instruction": SYSTEM_INSTRUCTION,
            "generation_config": GENERATION_CONFIG,
        }
        headers = {"Content-Type": "application/json"}

        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()

        data = response.json()
        logging.info(f"📩 Full Gemini API Response: {json.dumps(data, indent=2)}")

        candidates = data.get("candidates", [])
        if not candidates:
            return None

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if not parts:
            return None

        response_text = parts[-1].get("text", "").strip()
        logging.info(f"📩 Extracted response text: {response_text}")

        # Check for the fallback message
        if "I cannot determine if a leave form fill-up is required" in response_text:
            return ["I cannot determine if a leave form fill-up is required."]

        json_match = re.search(r"```json\n(.*?)\n```", response_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))

        logging.warning("⚠️ No JSON found in Gemini response.")
        return None

    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error calling Gemini API: {e}")
        return None

def handle_leaves_management_event(event):
    """
    Handles leave announcements in Slack threads and logs them into Google Sheets.
    """
    try:
        user_id = event.get("user", "")
        channel = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")  # Use thread_ts if available, else ts
        mentioned_text = event.get("text", "")

        IST = timezone("Asia/Kolkata")  
        today_date = datetime.now(IST).strftime("%d/%m/%Y")

        slack_user_name = get_slack_user_name(user_id)
        
        # Build thread context
        thread_history = fetch_thread_history(channel, thread_ts, exclude_ts=None)  # Fetch all messages in thread
        thread_history.append(f"{slack_user_name}: {mentioned_text}")
        
        # Add context to the prompt to indicate thread messages
        thread_context = "\n".join([f"Message {i+1}: {msg}" for i, msg in enumerate(thread_history)])
        prompt = f"Today's date is {today_date}. Use this when required.\nThe following messages are part of a Slack thread:\n{thread_context}"
        ai_response = get_gemini_response(prompt)

        if not ai_response or not isinstance(ai_response, list) or ai_response[0] == "I cannot determine if a leave form fill-up is required.":
            send_threaded_reply(channel, thread_ts, "I cannot determine if a leave form fill-up is required.")
            return json.dumps({"status": "processing_failed"}), 200

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

        for leave in leave_entries:
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

    except Exception as e:
        logging.error(f"❌ Error in handle_leaves_management_event: {e}")
        return json.dumps({"error": str(e)}), 500