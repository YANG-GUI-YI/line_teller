import os

from fastapi import FastAPI, Request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI


app = FastAPI()
conversation_history = {}
MAX_HISTORY_MESSAGES = 20
SYSTEM_PROMPT = "你是一位溫柔且有耐心的高齡陪伴助手，請根據對話上下文自然回覆。"

line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
client = OpenAI(
    api_key=os.environ["OPENROUTER_API_KEY"],
    base_url="https://openrouter.ai/api/v1",
)


def get_history(user_id):
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    return conversation_history[user_id]


def trim_history(user_id):
    conversation_history[user_id] = conversation_history[user_id][-MAX_HISTORY_MESSAGES:]


def build_messages(user_id):
    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        *conversation_history[user_id],
    ]


@app.post("/callback")
async def callback(request: Request):
    signature = request.headers["X-Line-Signature"]

    body = await request.body()
    body = body.decode("utf-8")

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return {"status": "invalid signature"}

    return {"status": "ok"}


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    user_id = event.source.user_id
    history = get_history(user_id)

    history.append({"role": "user", "content": user_message})
    trim_history(user_id)

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b:free",
            messages=build_messages(user_id),
        )

        reply_text = response.choices[0].message.content
        history.append({
            "role": "assistant",
            "content": reply_text,
        })
        trim_history(user_id)
    except Exception as e:
        print(e)
        reply_text = "目前系統忙碌中，請稍後再試。"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text),
    )
