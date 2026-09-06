import os
import re
import time

from flask import Flask, abort, request
from openai import OpenAI

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import JoinEvent, MessageEvent, TextMessageContent


app = Flask(__name__)

configuration = Configuration(
    access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
)
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])

# The OpenAI SDK reads OPENAI_API_KEY from Render's environment variables.
openai_client = OpenAI(timeout=20.0, max_retries=1)
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-terra")
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", "").strip()
PERMANENT_GROUPS = {
    group_id.strip()
    for group_id in os.environ.get("ALLOWED_GROUP_IDS", "").split(",")
    if group_id.strip()
}
runtime_allowed_groups = set()
runtime_blocked_groups = set()
group_access_cache = {}
GROUP_ACCESS_CACHE_SECONDS = 300


@app.route("/", methods=["GET"])
def home():
    return "HUY Translation Bot is running!"


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


def contains_chinese(text):
    return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))


def reply_text(event, text):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=text[:4900])],
            )
        )


def owner_is_in_group(group_id):
    """Allow a group automatically when the configured owner is a member."""
    if not ADMIN_USER_ID:
        app.logger.error("ADMIN_USER_ID is missing; automatic group access is disabled")
        return False

    now = time.monotonic()
    cached = group_access_cache.get(group_id)
    if cached and cached[1] > now:
        return cached[0]

    allowed = False
    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).get_group_member_profile(
                group_id=group_id,
                user_id=ADMIN_USER_ID,
            )
        allowed = True
    except Exception:
        # Fail closed: if LINE cannot confirm the owner is in the group,
        # do not translate messages for that group.
        app.logger.info("Owner is not confirmed in group %s", group_id)

    group_access_cache[group_id] = (
        allowed,
        now + GROUP_ACCESS_CACHE_SECONDS,
    )
    return allowed


def is_group_allowed(group_id, user_id=""):
    if group_id in runtime_blocked_groups:
        return False
    if group_id in PERMANENT_GROUPS or group_id in runtime_allowed_groups:
        return True
    # Safe fallback when LINE cannot return the owner's group profile:
    # a message sent by the configured owner proves the owner is in this group.
    if user_id == ADMIN_USER_ID:
        runtime_allowed_groups.add(group_id)
        group_access_cache.pop(group_id, None)
        return True
    return owner_is_in_group(group_id)


@handler.add(JoinEvent)
def handle_join(event):
    group_id = getattr(event.source, "group_id", None)
    if not group_id:
        return

    if owner_is_in_group(group_id):
        reply_text(
            event,
            "HUY Translation is enabled automatically because the owner is "
            "a member of this group.",
        )
    else:
        reply_text(
            event,
            "This group is locked. HUY Translation works only in groups "
            "where the owner is a member.",
        )

    if ADMIN_USER_ID:
        try:
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).push_message(
                    PushMessageRequest(
                        to=ADMIN_USER_ID,
                        messages=[
                            TextMessage(
                                text=(
                                    "HUY Translation was added to a new group.\n"
                                    f"Group ID: {group_id}\n"
                                    "Translation will start automatically if you are "
                                    "a member of that group."
                                )
                            )
                        ],
                    )
                )
        except Exception:
            app.logger.exception("Could not notify the owner")


def translate_text(text):
    if contains_chinese(text):
        direction = "Translate the message into natural, clear Vietnamese."
    else:
        direction = "Translate the message into natural Traditional Chinese used in Taiwan."

    response = openai_client.responses.create(
        model=OPENAI_MODEL,
        instructions=(
            "You are HUY Translation, a professional Chinese-Vietnamese translator "
            "for Vietnamese workers and employers in Taiwan. "
            f"{direction} "
            "Preserve names, numbers, dates, addresses, shift times, company names, "
            "LINE mentions, and the original level of politeness. "
            "Return only the translation, with no explanation, labels, or quotation marks."
        ),
        input=text,
        max_output_tokens=1200,
    )

    translated = response.output_text.strip()
    if not translated:
        raise ValueError("OpenAI returned an empty translation")

    # LINE text messages are limited in length. Keep a safe margin.
    return translated[:4900]


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    original_text = event.message.text.strip()
    if not original_text:
        return

    user_id = getattr(event.source, "user_id", "")
    group_id = getattr(event.source, "group_id", None)
    room_id = getattr(event.source, "room_id", None)
    command = original_text.lower().replace(" ", "")

    if group_id:
        if command == "/kichhoat":
            if user_id != ADMIN_USER_ID:
                reply_text(event, "Only the owner can activate this translation bot.")
                return
            runtime_allowed_groups.add(group_id)
            runtime_blocked_groups.discard(group_id)
            group_access_cache.pop(group_id, None)
            reply_text(
                event,
                "Translation enabled for this group.\n"
                f"Group ID: {group_id}\n"
                "No Render update is needed while the owner remains in the group.",
            )
            return

        if command in {"/khoa", "/tatdich"}:
            if user_id != ADMIN_USER_ID:
                reply_text(event, "Only the owner can lock this translation bot.")
                return
            runtime_allowed_groups.discard(group_id)
            runtime_blocked_groups.add(group_id)
            group_access_cache.pop(group_id, None)
            reply_text(event, "Translation has been locked for this group.")
            return

        if command == "/roinhom":
            if user_id != ADMIN_USER_ID:
                reply_text(event, "Only the owner can remove this translation bot.")
                return
            reply_text(event, "HUY Translation is leaving this group.")
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).leave_group(group_id)
            return

        if not is_group_allowed(group_id, user_id):
            return

    elif room_id:
        return

    try:
        translated_text = translate_text(original_text)
    except Exception:
        app.logger.exception("Translation failed")
        translated_text = "Translation failed. Please try again later."

    try:
        reply_text(event, translated_text)
    except Exception:
        app.logger.exception("LINE reply failed")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
