# article_saver.py
import logging
from datetime import datetime
from pytz import timezone
from google_sheets_writer import write_to_google_sheets
from slack_utils import get_slack_user_name, send_threaded_reply, fetch_thread_history
from secret_utils import get_secret
import requests
import json
import re

SHEET_ID = "1VcdPdcMJlsijN-M87UK36ZNvIPzCjbTrQN4Y1V0dNi4"
SHEET_NAME = "Article Repository"

# --- Function Gemini can call ---

def save_article_to_sheet(url: str, tags: list, submitted_by: str, submitted_on: str):
    try:
        tags_string = ", ".join(tags)
        row = [submitted_on, submitted_by, url, tags_string]
        success = write_to_google_sheets(SHEET_ID, SHEET_NAME, row)

        if success:
            logging.info(f"✅ Article saved: {url} by {submitted_by}")
            return {"status": "saved", "message": "📚 Article saved to internal repository."}
        else:
            return {"status": "error", "message": "Failed to save the article to the sheet."}

    except Exception as e:
        logging.error(f"❌ Error in save_article_to_sheet: {e}")
        return {"status": "error", "message": "Something went wrong while saving the article."}


# --- Gemini Function Definitions ---

ARTICLE_FUNCTIONS = [
    {
        "name": "save_article_to_sheet",
        "description": "Save an article with tags and metadata to a Google Sheet repository.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The article URL"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags related to the article"
                },
                "submitted_by": {"type": "string", "description": "Slack user who submitted it"},
                "submitted_on": {"type": "string", "description": "Date in DD/MM/YYYY format"}
            },
            "required": ["url", "tags", "submitted_by", "submitted_on"]
        }
    }
]


# --- Clean Slack text for Gemini ---
def clean_slack_text(text):
    # Remove mentions (like <@U07RL8UCZGB>)
    text = re.sub(r"<@([A-Z0-9]+)>", "", text)
    # Replace Slack links: <url|label> or <url> → url
    text = re.sub(r"<(https?://[^|>]+)(\\|[^>]+)?>", r"\1", text)
    return text.strip()
    

# --- Handle Slack message and process Gemini response ---
def get_gemini_response_with_functions(prompt):
    try:
        MODEL = "gemini-1.5-flash"
        SYSTEM_INSTRUCTION = {
            "parts": [
                {
                    "text": """
You are Nautifier, a Slack bot added to a channel where team members share useful articles for a weekly industry newsletter related to Analytics.

If someone shares a link and says anything that indicates it's valuable (e.g., “important”, “useful”, “bookmark this”, “newsletter-worthy”), assume they want it saved.

Also, if someone replies in a thread with a message asking you to save something, assume it refers to the earlier shared article in that thread. 

Use the `save_article_to_sheet` function to log the article with:
- URL
- Tags (either mentioned or inferred). Infer the topics the article is about and use as tags.
- Name of the user who submitted it
- Current date in DD/MM/YYYY format

If the message is ambiguous, but clearly includes a link and positive sentiment, err on the side of saving it.
"""
                }
            ]
        }

        api_key = get_secret("GEMINI_API_KEY")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"function_declarations": ARTICLE_FUNCTIONS}],
            "system_instruction": SYSTEM_INSTRUCTION
        }

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        # Check if Gemini wants to call a function
        if result.get("candidates"):
            candidate = result["candidates"][0]
            function_call = candidate.get("content", {}).get("parts", [])[0].get("functionCall")
            if function_call:
                name = function_call.get("name")
                args = function_call.get("args", {})
                if isinstance(args, str):
                    args = json.loads(args)
                if name == "save_article_to_sheet":
                    return save_article_to_sheet(**args)

        return {"status": "skipped", "message": "No function was called."}

    except Exception as e:
        logging.error(f"❌ Gemini function calling failed: {e}")
        return {"status": "error", "message": "Failed to process article saving request."}


# --- Slack Event Handler ---
def handle_article_saving_event(event):
    try:
        user_id = event.get("user")
        raw_text = event.get("text", "")
        text = clean_slack_text(raw_text)
        ts = event.get("ts")
        channel = event.get("channel")

        # Check for thread reply context
        thread_ts = event.get("thread_ts") or ts
        is_thread_reply = thread_ts != ts

        IST = timezone("Asia/Kolkata")
        today = datetime.now(IST).strftime("%d/%m/%Y")

        user_name = get_slack_user_name(user_id)

        if is_thread_reply:
            # Fetch parent message from the thread to build prompt
            thread_messages = fetch_thread_history(channel, thread_ts)
            context = "\n".join(thread_messages)
            prompt = f"Today's date is {today}. The following thread is the context:\n{context}\n{user_name} replied: {text}"
        else:
            prompt = f"Today's date is {today}. This message is from {user_name}: {text}"

        gemini_result = get_gemini_response_with_functions(prompt)
        send_threaded_reply(channel, ts, gemini_result.get("message", "✅ Processed."))

        return json.dumps({"status": gemini_result.get("status")}), 200

    except Exception as e:
        logging.error(f"❌ Error in handle_article_saving_event: {e}")
        return json.dumps({"error": str(e)}), 500