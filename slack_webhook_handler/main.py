import json
import google.cloud.logging
import logging
import google.cloud.tasks_v2
from google.cloud.tasks_v2 import HttpMethod
from google.cloud import firestore
import os
import time
from google.api_core import exceptions

# Set up Google Cloud Logging
client = google.cloud.logging.Client()
client.setup_logging()

# Cloud Tasks setup
tasks_client = google.cloud.tasks_v2.CloudTasksClient()
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "viraj-lab")
LOCATION = os.getenv("GCP_REGION", "us-central1")
QUEUE_NAME = os.getenv("CLOUD_TASKS_QUEUE", "slack-event-queue")
EVENT_HANDLER_URL = os.getenv("EVENT_HANDLER_URL")

# Validate environment variables
if not EVENT_HANDLER_URL:
    logging.error("❌ EVENT_HANDLER_URL environment variable is not set.")
    raise ValueError("EVENT_HANDLER_URL environment variable must be set.")

# Firestore setup
try:
    db = firestore.Client(database="nautifier-db")
except Exception as e:
    logging.error(f"❌ Failed to initialize Firestore client for database 'nautifier-db': {e}")
    raise ValueError(f"Failed to initialize Firestore client for database 'nautifier-db': {e}")

def create_cloud_task(event_data):
    """
    Sends event processing to Cloud Tasks for async execution (in production).
    Returns True if the task was created, False if it was already queued or failed.
    """
    try:
        event_id = event_data.get("event_id") or event_data.get("event_ts")
        if not event_id:
            logging.error("❌ No event_id or event_ts found in event data.")
            return False

        # Use Firestore to check if event was already queued
        event_ref = db.collection("processed_events").document(event_id)

        # Attempt to create the document atomically (fails if it already exists)
        try:
            event_ref.create({
                "queued_at": firestore.SERVER_TIMESTAMP,
                "status": "queued"
            })
        except exceptions.AlreadyExists:
            logging.info(f"✅ Event {event_id} already queued, skipping.")
            return False
        except Exception as e:
            logging.error(f"❌ Failed to write to Firestore for event {event_id}: {e}")
            return False

        # Create Cloud Task
        try:
            parent = tasks_client.queue_path(PROJECT_ID, LOCATION, QUEUE_NAME)
            task = {
                "http_request": {
                    "http_method": HttpMethod.POST,
                    "url": EVENT_HANDLER_URL,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps(event_data).encode(),
                }
            }
            response = tasks_client.create_task(parent=parent, task=task)
            logging.info(f"✅ Cloud Task created: {response.name} targeting {EVENT_HANDLER_URL}")
            return True
        except Exception as e:
            logging.error(f"❌ Failed to create Cloud Task for event {event_id}: {e}")
            # Roll back the Firestore document if Cloud Task creation fails
            try:
                event_ref.delete()
                logging.info(f"✅ Rolled back Firestore document for event {event_id} due to Cloud Task failure.")
            except Exception as delete_e:
                logging.error(f"❌ Failed to roll back Firestore document for event {event_id}: {delete_e}")
            return False

    except Exception as e:
        logging.error(f"❌ Unexpected error in create_cloud_task for event {event_id}: {e}")
        return False

def slack_webhook_handler(request):
    """
    Handles Slack events, acknowledges immediately, and offloads processing.
    """
    try:
        payload = request.get_json()
        if not payload:
            logging.error("❌ No JSON payload received from Slack.")
            return json.dumps({"error": "No JSON payload"}), 400

        # Handle Slack URL verification
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            return challenge, 200, {"Content-Type": "text/plain"}

        # Process event callbacks
        if payload.get("type") == "event_callback":
            event = payload.get("event", {})
            # Respond immediately to Slack to avoid retries
            response = json.dumps({"status": "queued"}), 200

            # Log the event after preparing the response
            logging.info(f"📌 Event received: {json.dumps(event, indent=2)}")
            logging.info("🚀 Production Mode: Queuing event in Cloud Tasks.")

            # Queue the event in Cloud Tasks
            if create_cloud_task(event):
                logging.info("✅ Event successfully queued in Cloud Tasks.")
            else:
                logging.info("✅ Event already queued or failed to queue.")
                response = json.dumps({"status": "already_queued_or_error"}), 200

            return response

        logging.warning("⚠️ Unsupported event type received.")
        return json.dumps({"status": "unsupported_event"}), 200

    except Exception as e:
        logging.error(f"❌ Error processing Slack webhook: {e}")
        return json.dumps({"error": str(e)}), 500