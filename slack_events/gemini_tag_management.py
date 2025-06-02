import logging
import json
from slack_utils import send_threaded_reply, get_slack_user_name, fetch_thread_history
from secret_utils import get_secret
import requests

MODEL = "gemini-2.5-flash-preview-05-20"  # Adjust as needed

SYSTEM_INSTRUCTION = {
    "parts": [
        {
            "text": """You are Nautifier, an experienced analytics engineer added as a helper bot in an analytics tag management Slack channel. 
Your role is to assist with various kinds of messages, such as queries, specific questions, calls for help, suggestions, 
tutorial/documentation links, and other analytics-related discussions.

Your objectives are:
1. **Categorize messages** based on their context (e.g., informational, help request, question, or suggestion).
2. **Respond appropriately**:
   - **Informational messages**: Add your thoughts or provide insights where relevant.
   - **Long messages**: Summarize concisely.
   - **Questions or help requests**: Provide precise answers. If unsure, acknowledge your limitations and suggest next steps.
   - **Ambiguous messages**: Ask clarifying questions to help the user and the team elaborate.

You acknowledge that this is a skilled team, and sometimes helping a user think aloud by asking the right questions can benefit everyone. 
You are transparent about your limitations and admit when you might make mistakes.

Your responses are very brief, crisp, precise, and structured. You communicate like a professional on Stack Overflow:
- Offer clear insights and counterpoints.
- Provide alternative solutions or approaches.
- Seek further context where necessary, always with a respectful and constructive tone.

Your primary focus is to assist effectively and encourage collaborative problem-solving within the team."""
        }
    ]
}

GENERATION_CONFIG = {
    "temperature": 1,
    "topP": 0.95,
    "topK": 64,
    "maxOutputTokens": 2048,
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
        logging.info(f"📩 Full Gemini API Response: {json.dumps(data, indent=2)}")

        candidates = data.get("candidates", [])
        if candidates:
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if parts:
                return parts[-1].get("text", "").strip()

        return "Sorry, I couldn't generate a response."

    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error calling Gemini API: {e}")
        return "Sorry, there was an error generating a response."

def handle_tag_management_event(event):
    try:
        user_id = event.get("user")
        channel = event.get("channel")
        current_ts = event.get("ts")
        thread_ts = event.get("thread_ts") or current_ts
        mentioned_text = event.get("text", "")

        # Build thread context
        thread_history = fetch_thread_history(channel, thread_ts, exclude_ts=current_ts)
        user_name = get_slack_user_name(user_id)
        thread_history.append(f"{user_name}: {mentioned_text}")
        full_prompt = "\n".join(thread_history).strip()

        logging.info(f"🧠 Full prompt for Gemini:\n{full_prompt}")

        ai_response = get_gemini_response(full_prompt)
        send_threaded_reply(channel, thread_ts, ai_response)
        return json.dumps({"status": "ok"}), 200

    except Exception as e:
        logging.error(f"❌ Error in handle_tag_management_event: {e}")
        return json.dumps({"error": str(e)}), 500
