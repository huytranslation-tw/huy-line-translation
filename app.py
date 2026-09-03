import os
import re

from flask import Flask, request, abort
from deep_translator import GoogleTranslator

from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError


app = Flask(__name__)

configuration = Configuration(
    access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
)

handler = WebhookHandler(
    os.environ["LINE_CHANNEL_SECRET"]
)


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


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    original_text = event.message.text.strip()

    try:
        # Chinese -> Vietnamese
        if contains_chinese(original_text):
            translated_text = GoogleTranslator(
                source="auto",
                target="vi"
            ).translate(original_text)

        # Vietnamese/other text -> Traditional Chinese
        else:
            translated_text = GoogleTranslator(
                source="auto",
                target="zh-TW"
            ).translate(original_text)

    except Exception as e:
        app.logger.exception(e)
        translated_text = "Dịch thất bại. Vui lòng thử lại sau."

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(text=translated_text)
                ]
            )
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
