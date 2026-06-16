import asyncio
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI

from medical_rag import format_medical_context
from weather_push import get_next_weather_push_time, weather_notification_loop


load_dotenv()

app = FastAPI()
conversation_history = {}
weather_push_task = None
MAX_HISTORY_MESSAGES = 20
RAG_RESULT_LIMIT = 3
SYSTEM_PROMPT = (
    "You are a gentle and patient companion for older adults. "
    "For elderly medical questions, provide health education, care reminders, "
    "and directions for seeking care only. Do not diagnose, prescribe medicine, "
    "change medication doses, or replace a clinician. If the user describes "
    "emergency warning signs such as chest pain, trouble breathing, fainting, "
    "sudden one-sided weakness, speech difficulty, confusion, severe headache, "
    "head injury, heavy bleeding, or suspected fracture, tell them to contact "
    "local emergency services immediately. In Taiwan, they can call 119."
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
                "The following elderly-medical RAG context was retrieved. "
                "Use it first when relevant, mention the source name briefly when useful, "
                "and say that a clinician or urgent care evaluation is needed when the "
                "context is insufficient.\n\n"
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


@app.get("/")
async def root():
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/wake")
async def wake():
    next_weather_push = get_next_weather_push_time().isoformat()
    return {
        "status": "awake",
        "next_weather_push": next_weather_push,
    }


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
        reply_text = "The system is busy right now. Please try again later."

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text),
    )
