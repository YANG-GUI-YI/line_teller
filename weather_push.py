import asyncio
import json
import os
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen


TAIWAN_TZ = timezone(timedelta(hours=8), name="UTC+8")
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
DEFAULT_WEATHER_LAT = "25.0330"
DEFAULT_WEATHER_LON = "121.5654"
DEFAULT_WEATHER_LOCATION_NAME = "Taipei"


def get_push_targets():
    targets = os.getenv("LINE_WEATHER_PUSH_TO", "")
    return [target.strip() for target in targets.split(",") if target.strip()]


def get_next_weather_push_time(now=None):
    now = now or datetime.now(TAIWAN_TZ)
    next_run = datetime.combine(now.date(), time(hour=8), tzinfo=TAIWAN_TZ)
    if now >= next_run:
        next_run += timedelta(days=1)
    return next_run


def fetch_current_weather():
    api_key = os.environ["OPENWEATHER_API_KEY"]
    params = {
        "lat": os.getenv("WEATHER_LAT", DEFAULT_WEATHER_LAT),
        "lon": os.getenv("WEATHER_LON", DEFAULT_WEATHER_LON),
        "appid": api_key,
        "units": "metric",
        "lang": os.getenv("OPENWEATHER_LANG", "zh_tw"),
    }
    request_url = f"{OPENWEATHER_URL}?{urlencode(params)}"

    with urlopen(request_url, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def format_weather_message(weather_data):
    location_name = os.getenv("WEATHER_LOCATION_NAME") or weather_data.get("name") or DEFAULT_WEATHER_LOCATION_NAME
    weather = weather_data.get("weather", [{}])[0]
    main = weather_data.get("main", {})
    wind = weather_data.get("wind", {})
    rain = weather_data.get("rain", {})

    description = weather.get("description", "weather data unavailable")
    temperature = main.get("temp")
    feels_like = main.get("feels_like")
    humidity = main.get("humidity")
    wind_speed = wind.get("speed")
    rain_1h = rain.get("1h", 0)

    lines = [
        f"Good morning. Here is the 08:00 weather update for {location_name}:",
        f"Weather: {description}",
    ]

    if temperature is not None:
        lines.append(f"Temperature: {temperature:.1f} C")
    if feels_like is not None:
        lines.append(f"Feels like: {feels_like:.1f} C")
    if humidity is not None:
        lines.append(f"Humidity: {humidity}%")
    if wind_speed is not None:
        lines.append(f"Wind speed: {wind_speed:.1f} m/s")
    if rain_1h:
        lines.append(f"Rain in the last hour: {rain_1h} mm")

    lines.extend([
        "",
        "Older-adult reminder: watch for slippery ground and temperature changes. Drink water regularly. If chest tightness, trouble breathing, or obvious discomfort occurs, contact family or seek medical care.",
    ])
    return "\n".join(lines)


def push_weather_message(line_bot_api):
    from linebot.models import TextSendMessage

    targets = get_push_targets()
    if not targets:
        print("LINE_WEATHER_PUSH_TO is not set; skip weather push.")
        return

    weather_data = fetch_current_weather()
    message = TextSendMessage(text=format_weather_message(weather_data))
    for target in targets:
        line_bot_api.push_message(target, message)


async def weather_notification_loop(line_bot_api):
    if os.getenv("WEATHER_PUSH_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        print("Weather push is disabled.")
        return

    while True:
        next_run = get_next_weather_push_time()
        sleep_seconds = (next_run - datetime.now(TAIWAN_TZ)).total_seconds()
        await asyncio.sleep(max(sleep_seconds, 0))

        try:
            await asyncio.to_thread(push_weather_message, line_bot_api)
        except Exception as exc:
            print(f"Weather push failed: {exc}")
