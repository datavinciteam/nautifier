import json
import google.cloud.logging
import logging
from google.cloud import firestore
from gemini_tag_management import handle_tag_management_event
from gemini_leaves_management import handle_leaves_management_event
from article_saver import handle_article_saving_event

# Set up Google Cloud Logging
client = google.cloud.logging.Client()
client.setup_logging()
logging.basicConfig(level=logging.INFO)
logging.info("✅ Logging initialized successfully in slack_events!")

# Firestore setup with explicit database name
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "viraj-lab")
db = firestore.Client(project=PROJECT_ID, database="nautifier-db")

# Channel IDs
NAUTIFIER_SANDBOX = "C07R5QU0LKZ"
NAUTIFIER_SANDBOX_CHANNEL = "C08KR42C85C"  # Nautifier sandbox public channel
WEEKLY_INDUSTRY_UPDATES_CHANNEL = "C08AEB7H0JE"  # weekly-industry-updates channel ID
TAG_MANAGEMENT_CHANNEL = "C01SK3F164U"  # Tag Management Channel ID
LEAVES_CHANNEL = NAUTIFIER_SANDBOX_CHANNEL # "C04HY5GR91B"  # Leaves Management Channel ID.

def slack_event_processor(request):
    """
    Processes Slack events forwarded from Cloud Tasks.
    """
    try:
        payload = request.get_json()
        
        # Handle events without an 'event' wrapper
        if not isinstance(payload, dict):
            logging.error(f"❌ Invalid event format received: {json.dumps(payload, indent=2)}")
            return json.dumps({"status": "invalid_event"}), 200

        logging.info(f"📩 Processing event from Cloud Tasks: {json.dumps(payload, indent=2)}")

        # Extract event data
        event = payload if "event" not in payload else payload.get("event", {})
        event_id = event.get("event_id") or event.get("event_ts")
        if not event_id:
            logging.error("❌ No event_id or event_ts found in event data.")
            return json.dumps({"status": "missing_event_id"}), 200

        # Use Firestore transaction to check and process event
        event_ref = db.collection("processed_events").document(event_id)
        @firestore.transactional
        def process_event(transaction):
            snapshot = event_ref.get(transaction=transaction)
            if snapshot.exists and snapshot.get("status") == "completed":
                logging.info(f"✅ Event {event_id} already processed, skipping.")
                return json.dumps({"status": "already_processed"}), 200

            # Process the event
            channel = event.get("channel")
            logging.info(f"✅ Processing event in channel: {channel}, Event ID: {event_id}")

            if channel == TAG_MANAGEMENT_CHANNEL:
                result = handle_tag_management_event(event)
            elif channel == LEAVES_CHANNEL:
                result = handle_leaves_management_event(event)
            elif channel == WEEKLY_INDUSTRY_UPDATES_CHANNEL:
                result = handle_article_saving_event(event)
            else:
                result = json.dumps({"status": "ok"}), 200

            # Update status to completed
            transaction.update(event_ref, {
                "processed_at": firestore.SERVER_TIMESTAMP,
                "status": "completed"
            })
            return result

        return process_event(db.transaction())

    except Exception as e:
        logging.error(f"❌ Error processing event: {e}")
        return json.dumps({"error": str(e)}), 200