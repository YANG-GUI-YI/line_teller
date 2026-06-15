import os
import asyncio

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI

from medical_rag import format_medical_context
from weather_push import weather_notification_loop


load_dotenv()

app = FastAPI()
conversation_history = {}
weather_push_task = None
MAX_HISTORY_MESSAGES = 20
RAG_RESULT_LIMIT = 3
SYSTEM_PROMPT = (
    "你是一位溫柔且有耐心的高齡陪伴助手。"
    "回答老人醫療相關問題時，只提供衛教、照護提醒與就醫方向，不做確定診斷，"
    "不開藥、不調整藥量，也不取代醫師。"
    "若使用者描述胸痛、呼吸困難、昏倒、突然單側無力、說話困難、意識混亂、"
    "嚴重頭痛、頭部外傷、大量出血、疑似骨折或其他急症警訊，"
    "請優先建議立即聯絡當地緊急救護；在台灣可撥打119。"
)

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


def build_messages(user_id, user_message):
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    ]

    medical_context = format_medical_context(user_message, limit=RAG_RESULT_LIMIT)
    if medical_context:
        messages.append({
            "role": "system",
            "content": (
                "以下是老人醫療 RAG 檢索到的參考資料。"
                "請優先依據這些資料回答，並在適合時簡短提到來源名稱。"
                "若資料不足，請明確說明需要詢問醫師或就醫評估。\n\n"
                f"{medical_context}"
            ),
        })

    messages.extend(conversation_history[user_id])
    return messages


@app.on_event("startup")
async def startup_event():
    global weather_push_task
    weather_push_task = asyncio.create_task(weather_notification_loop(line_bot_api))


@app.on_event("shutdown")
async def shutdown_event():
    if weather_push_task:
        weather_push_task.cancel()


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
            messages=build_messages(user_id, user_message),
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
