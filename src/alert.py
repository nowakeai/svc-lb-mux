import logging
import os

from slack_sdk import WebhookClient

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
slack = WebhookClient(SLACK_WEBHOOK_URL) if SLACK_WEBHOOK_URL else None


def slack_alert(level, title, message):
    logging.info(f"Sending {level} alert: {title} - {message}")
    if not slack:
        logging.warning("Slack webhook URL not set, skipping alert")
        return
    if level == "info":
        title = f"ℹ️ {title}"
    elif level == "warn":
        title = f"⚠️ {title}"
    elif level == "error":
        title = f"❌ {title}"
    return slack.send(
        text="",
        blocks=[
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": title,
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message,
                },
            },
        ],
    )
