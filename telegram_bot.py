import os
import json
from openai import OpenAI
from google.oauth2.service_account import Credentials
import gspread
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackContext
)
import logging
from dotenv import load_dotenv
import pytz
import io
from google.cloud import speech

# Установим часовой пояс "Europe/Kiev"
kiev_timezone = pytz.timezone("Europe/Kiev")

# Загрузка переменных окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация OpenAI клиента
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Настройка Google Sheets
SCOPE = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
CREDS = Credentials.from_service_account_info(json.loads(os.getenv("GOOGLE_CREDENTIALS")), scopes=SCOPE)
gc = gspread.authorize(CREDS)
worksheet = gc.open_by_key(os.getenv("SPREADSHEET_ID")).sheet1

# Настройка Google Speech-to-Text клиента
speech_client = speech.SpeechClient(credentials=CREDS)

async def transcribe_audio(file_path):
    with io.open(file_path, "rb") as audio_file:
        content = audio_file.read()
    
    audio = speech.RecognitionAudio(content=content)
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.OGG_OPUS,
        language_code="ru-RU"
    )
    
    response = speech_client.recognize(config=config, audio=audio)
    
    for result in response.results:
        return result.alternatives[0].transcript
    return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.voice:
        # Обработка голосового сообщения
        file = await update.message.voice.get_file()
        file_path = "voice_message.ogg"
        await file.download_to_drive(file_path)
        
        user_message = await transcribe_audio(file_path)
        if not user_message:
            await update.message.reply_text("Не удалось распознать голосовое сообщение.")
            return
    else:
        # Обработка текстового сообщения
        user_message = update.message.text

    logger.info(f"Received message: {user_message}")
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты помощник, который извлекает из сообщения следующие данные: "
                        "'client' (имя отправителя, клиента, проекта или компании, которое может быть ТОЛЬКО на кириллице. Ты должен понимать реальные имена людей)"
                        "'description' (описание действия, включающее всю информацию (и ссылки и слова на латинице и криллице) после имени клиента или отправителя, кроме времени и суммы), "
                        "'time_spent' (затраченное время) и 'amount' (сумма в гривнах). "
                        "Ты должен сохранять все символы в 'description', включая латинские буквы, цифры, знаки препинания и ссылки. "
                        "Не опускай никакие части текста. "
                        "Если какой-либо из данных отсутствует, используй '-'. "
                        "Выводи данные только в формате JSON без дополнительного текста."
                    )
                },
                {
                    "role": "user",
                    "content": f"Извлеки данные из следующего сообщения: '{user_message}'. "
                               "Выведи их в формате JSON с ключами 'description', 'client', 'time_spent', 'amount'."
                }
            ],
            temperature=0
        )


        logger.info(f"OpenAI response: {response}")

        chatgpt_response = response.choices[0].message.content

        try:
            data = json.loads(chatgpt_response)
        except json.JSONDecodeError:
            logger.error("Response is not in JSON format. Processing as plain text.")
            data = {"description": chatgpt_response, "client": "-", "time_spent": "-", "amount": "-"}

        description = data.get('description', '-')
        client_name = data.get('client', '-')
        time_spent = data.get('time_spent', '-')
        amount = data.get('amount', '-')
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        description, client_name, time_spent, amount = user_message, '-', '-', '-'

    # Запись в Google Sheets
    current_datetime = datetime.now(kiev_timezone).strftime("%Y-%m-%d %H:%M:%S")
    row = [current_datetime, description, client_name, time_spent, amount]
    try:
        worksheet.append_row(row)
        # Формирование ответа для пользователя
        reply_text = f"📆 {current_datetime}\n💬 {description}\n🧑‍💼 {client_name}"

        if time_spent != '-' and time_spent.strip():
            reply_text += f"\n🕝 {time_spent}"

        if amount != '-' and amount.strip():
            reply_text += f"\n💰 {amount}"

        await update.message.reply_text(reply_text)
    except gspread.exceptions.APIError as ge:
        logger.error(f"Google Sheets API Error: {ge}")
        await update.message.reply_text('Ошибка при добавлении данных в таблицу.')

def error_handler(update: Update, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

def main():
    application = ApplicationBuilder().token(os.getenv("TELEGRAM_TOKEN")).build()
    application.add_handler(CommandHandler("start", handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_message))
    application.add_error_handler(error_handler)
    application.run_polling()

if __name__ == "__main__":
    main()