import json
import google.cloud.logging
import logging
import time
from gemini_tag_management import handle_tag_management_event
from gemini_leaves_management import handle_leaves_management_event
from article_saver import handle_article_saving_event


# Set up Google Cloud Logging
client = google.cloud.logging.Client()
client.setup_logging()
logging.basicConfig(level=logging.INFO)  # Ensure logging is set
logging.info("✅ Logging initialized successfully in slack_events!")

# Channel IDs
NAUTIFIER_SANDBOX = "C07R5QU0LKZ"
NAUTIFIER_SANDBOX_CHANNEL = "C08KR42C85C" #Nautifier sandbox public channel
WEEKLY_INDUSTRY_UPDATES_CHANNEL = "C08AEB7H0JE" #weekly-industy-updates channel ID
TAG_MANAGEMENT_CHANNEL = "C01SK3F164U"  # Tag Management Channel ID
LEAVES_CHANNEL = NAUTIFIER_SANDBOX_CHANNEL #"C04HY5GR91B"  # Leaves Management Channel ID

# Temporary in-memory cache with timestamps to prevent duplicates
EVENT_CACHE = {}
EVENT_EXPIRY_TIME = 60  # Time in seconds to retain event timestamps before allowing reprocessing

def is_duplicate_event(event_id):
    """
    Checks if the event is a duplicate using a short-lived cache.
    """
    current_time = time.time()

    # Remove expired events
    expired_keys = [key for key, ts in EVENT_CACHE.items() if current_time - ts > EVENT_EXPIRY_TIME]
    for key in expired_keys:
        del EVENT_CACHE[key]

    # Check if event_id already exists
    if event_id in EVENT_CACHE:
        logging.info(f"🔄 Duplicate event detected: {event_id}. Skipping processing.")
        return True

    # Store event_id with timestamp
    EVENT_CACHE[event_id] = current_time
    return False

def slack_event_processor(request):
    """
    Processes Slack events forwarded from Cloud Tasks.
    """
    try:
        payload = request.get_json()
        
        # **🔹 Fix: Handle events without an 'event' wrapper**
        if not isinstance(payload, dict):
            logging.error(f"❌ Invalid event format received: {json.dumps(payload, indent=2)}")
            return json.dumps({"status": "invalid_event"}), 200  # Return 200 to stop retries
        
        logging.info(f"📩 Processing event from Cloud Tasks: {json.dumps(payload, indent=2)}")

        # **🔹 New: Extract event data correctly**
        event = payload if "event" not in payload else payload.get("event", {})
        event_id = event.get("ts") or event.get("event_ts")  # Use event_ts if ts is missing
        channel = event.get("channel")

        if not event_id:
            logging.warning("⚠️ Event received without a valid event ID. Skipping.")
            return json.dumps({"status": "missing_event_id"}), 200  # Return 200 to stop retries

        # **🔹 Fix: Prevent duplicate processing**
        if is_duplicate_event(event_id):
            return json.dumps({"status": "duplicate_skipped"}), 200  # Return 200 to avoid retries

        logging.info(f"✅ Processing event in channel: {channel}, Event ID: {event_id}")

        # **🔹 Fix: Properly route events to their handlers**
        if channel == TAG_MANAGEMENT_CHANNEL:
            return handle_tag_management_event(event)
        elif channel == LEAVES_CHANNEL:
            return handle_leaves_management_event(event)
        elif channel == WEEKLY_INDUSTRY_UPDATES_CHANNEL:
            return handle_article_saving_event(event)


        return json.dumps({"status": "ok"}), 200

    except Exception as e:
        logging.error(f"❌ Error processing event: {e}")
        return json.dumps({"error": str(e)}), 200  # **Return 200 to prevent Cloud Tasks from retrying**
