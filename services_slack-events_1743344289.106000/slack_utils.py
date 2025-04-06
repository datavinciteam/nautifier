import requests
from secret_utils import get_secret
import logging

SLACK_BOT_TOKEN = get_secret("SLACK_BOT_TOKEN")

def get_slack_user_name(user_id):
    """
    Fetches the real name or display name of a Slack user using their ID.
    """
    url = f"https://slack.com/api/users.info?user={user_id}"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}

    try:
        response = requests.get(url, headers=headers)
        response_data = response.json()

        if response_data.get("ok"):
            user_info = response_data.get("user", {})
            return user_info.get("profile", {}).get("real_name", f"<@{user_id}>")
        else:
            logging.warning(f"⚠️ Could not fetch Slack user info: {response_data}")
            return f"<@{user_id}>"

    except Exception as e:
        logging.error(f"❌ Error fetching Slack user name: {e}")
        return f"<@{user_id}>"

def send_threaded_reply(channel, thread_ts, text):
    """
    Sends a reply to a Slack message in a thread.
    """
    if not SLACK_BOT_TOKEN:
        logging.error("Slack Bot Token not found.")
        return

    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "channel": channel,
        "text": text,
        "thread_ts": thread_ts,
    }

    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200 and response.json().get("ok"):
        logging.info("✅ Threaded reply sent successfully.")
    else:
        logging.error(f"❌ Error sending threaded reply: {response.text}")
        

def fetch_thread_history(channel, thread_ts, exclude_ts=None):
    """
    Fetches all previous messages in a thread, excluding the message with `exclude_ts` (if given).
    Returns a list of "username: message" strings.
    """
    url = "https://slack.com/api/conversations.replies"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {
        "channel": channel,
        "ts": thread_ts,
        "limit": 100
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        data = response.json()

        if not data.get("ok"):
            logging.error(f"❌ Error from Slack API: {data}")
            return []

        messages = data.get("messages", [])
        logging.info(f"📚 Thread fetch: {len(messages)} messages returned")

        formatted_msgs = []
        for msg in messages:
            ts = msg.get("ts")
            if exclude_ts and ts == exclude_ts:
                continue

            user_id = msg.get("user") or msg.get("bot_id", "system")
            user_name = get_slack_user_name(user_id) if user_id.startswith("U") else "System"
            text = msg.get("text", "")
            formatted_msgs.append(f"{user_name}: {text}")

        logging.info("🧵 Formatted thread context:\n" + "\n".join(formatted_msgs))
        return formatted_msgs

    except Exception as e:
        logging.error(f"❌ Exception in fetch_thread_history: {e}")
        return []
