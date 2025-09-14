# Nautifier

Internal Slack app for automating tasks at datavinci. Handles leave management, tag management, and general chat using Gemini AI.

## Video Walkthrough

[Complete setup and architecture explanation](<https://www.youtube.com/watch?v=c7UUTwBK82A>)

## How it works

```
Slack mention → webhook handler → deduplication check → queue task → event processor → Gemini API → response
```

Two Cloud Functions:
- `slack-webhook-handler`: Lightweight function that receives Slack events and queues them
- `slack-events`: Processes events and calls Gemini API

## Setup

### GCP Project
- Project: `datavinci-laboratory` 
- Organization: datavinci.services
- Account: delivery

### Services used
- Cloud Functions (Python 3.10)
- Cloud Build (auto-deploy from GitHub)
- Cloud Tasks (event queuing)
- Firestore (`nautifier-db` for deduplication)
- Secret Manager (API keys)

### Deployment
Push to `main` branch auto-deploys via Cloud Build. Uses `cloudbuild.yaml` config.

## Repository structure

```
nautifier/
├── slack-webhook-handler/
│   └── main.py                 # Receives webhooks, queues tasks
├── slack-events/
│   ├── main.py                # Routes events by channel
│   ├── gemini_leaves_management.py   # Leave requests (Gemini 2.0 Flash)
│   ├── gemini_tag_management.py      # Analytics stuff (Gemini Preview)
│   ├── chatter-patter.py            # General chat (Gemini 2.5 Flash)
│   └── slack_utils.py        # Slack API helpers
└── cloudbuild.yaml           # Deployment config
```

## Channel handlers

Each channel uses different Gemini models and personalities:
- **Leaves**: HR bot, processes/cancels leave requests, logs to Google Sheets
- **Tag management**: Analytics engineer persona
- **General chat**: Casual conversation bot

## Architecture decisions

**Why two functions?**
- Slack requires response within 3 seconds
- Gemini API can take 10-15 seconds
- Webhook handler responds immediately, queues actual work
- Keeps costs low (₹10-15/month vs ₹200+)

**Deduplication**
- Slack retries failed webhooks
- Store event timestamps in Firestore
- Discard duplicate events

## Development

Clone and work locally:
```bash
git clone https://github.com/datavinciteam/nautifier.git
```

Configure delivery alias in VS Code, make changes, commit to main. Cloud Build handles the rest.

## Secrets (in Secret Manager)
- `slack_token`: From Slack app config
- `gemini_api_key`: From Google AI Studio
- `github_auth`: For Cloud Build integration

## Debugging

Check logs:
```bash
gcloud functions logs read slack-webhook-handler --project=datavinci-laboratory
```

Common issues:
- Duplicate events: Check Firestore processed_events collection
- Timeouts: Webhook handler should respond in <3s
- Bad AI responses: Adjust temperature in channel handlers

## Cost optimization

- Webhook handler: Always-on with minimal resources
- Event processor: Scales to zero when idle
- Cloud Tasks: Prevents resource waste

---

Forked from VirajPrateek/nautifier
