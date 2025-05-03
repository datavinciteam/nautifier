import json
import google.cloud.logging
import logging
import google.cloud.tasks_v2
from google.cloud.tasks_v2 import HttpMethod
import os

# Set up Google Cloud Logging
client = google.cloud.logging.Client()
client.setup_logging()

# Cloud Tasks setup
tasks_client = google.cloud.tasks_v2.CloudTasksClient()
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "viraj-lab")
LOCATION = os.getenv("GCP_REGION", "us-central1")
QUEUE_NAME = os.getenv("CLOUD_TASKS_QUEUE", "slack-event-queue")
EVENT_HANDLER_URL = os.getenv("EVENT_HANDLER_URL", "https://us-central1-viraj-lab.cloudfunctions.net/slack_events")

def create_cloud_task(event_data):
    """
    Sends event processing to Cloud Tasks for async execution (in production).
    """
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
        logging.error(f"❌ Failed to create Cloud Task: {e}")
        return False

def slack_webhook_handler(request):
    """
    Handles Slack events, acknowledges immediately, and offloads processing.
    """
    try:
        payload = request.get_json()
        logging.info(f"📩 Payload received from Slack: {json.dumps(payload, indent=2)}")

        # Handle Slack URL verification
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            logging.info("🔹 URL verification challenge received.")
            return challenge, 200, {"Content-Type": "text/plain"}

        # Process event callbacks
        if payload.get("type") == "event_callback":
            event = payload.get("event", {})
            logging.info(f"📌 Event received: {json.dumps(event, indent=2)}")

            # Production Mode: Queuing event in Cloud Tasks
            logging.info("🚀 Production Mode: Queuing event in Cloud Tasks.")
            if create_cloud_task(event):
                logging.info("✅ Event successfully queued in Cloud Tasks.")
                return json.dumps({"status": "queued"}), 200
            else:
                logging.error("❌ Failed to queue event in Cloud Tasks.")
                return json.dumps({"status": "error creating task"}), 500

        logging.warning("⚠️ Unsupported event type received.")
        return json.dumps({"status": "unsupported_event"}), 200

    except Exception as e:
        logging.error(f"❌ Error processing Slack webhook: {e}")
        return json.dumps({"error": str(e)}), 500
