import logging
import json
from slack_utils import send_threaded_reply, get_slack_user_name, fetch_thread_history
from secret_utils import get_secret
import requests
from datetime import datetime
from pytz import timezone

MODEL = "gemini-2.0-flash-lite"
SYSTEM_INSTRUCTION = {
    "parts": [
        {
            "text": """You are Nautifier, a fun Slack bot in the 'chattar-pattar' informal chats channel. When tagged (@Nautifier), respond with a playful, casual tone based on the thread context. Use the thread history to understand what’s been said by users and your previous replies. Keep responses short, witty, and relevant. If unsure, make a lighthearted guess or ask a fun follow-up question."""
        }
    ]
}

GENERATION_CONFIG = {
    "temperature": 1.0,
    "topP": 0.95,
    "topK": 64,
    "maxOutputTokens": 256,
    "responseMimeType": "text/plain",
}

def get_gemini_response(prompt):
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
        candidates = data.get("candidates", [])
        if candidates:
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if parts:
                return parts[-1].get("text", "").strip()

        return "Oops, I've run out of ideas! Throw me another one!"

    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error calling Gemini API: {e}")
        return "Yikes, something went wrong! Let's try that again later!"

def handle_chattar_pattar_event(event):
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

        # Fetch thread context
        thread_history = fetch_thread_history(channel, thread_ts, exclude_ts=None)
        thread_history.append(f"{slack_user_name}: {message}")
        thread_context = "\n".join([f"Message {i+1}: {msg}" for i, msg in enumerate(thread_history)])
        prompt = f"Today's date is {today_date}. Respond to this thread:\n{thread_context}"

        # Get AI response
        ai_response = get_gemini_response(prompt)
        send_threaded_reply(channel, thread_ts, ai_response)
        return json.dumps({"status": "ok"}), 200

    except Exception as e:
        logging.error(f"❌ Error in handle_chattar_pattar_event: {e}")
        send_threaded_reply(channel, thread_ts, f"Oops, {slack_user_name}, I tripped up! Try again or report this to Prateek!")
        return json.dumps({"error": str(e)}), 500