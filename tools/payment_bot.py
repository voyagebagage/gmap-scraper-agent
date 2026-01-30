import os
import io
import logging
import datetime
import json
import asyncio
import traceback
import smtplib
from email.message import EmailMessage
import pytz
from typing import Optional

import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

from google import genai
from google.genai import types
from telegram import Update, File
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
BOT_TOKEN = os.getenv("PAYMENTPHOTOTOACCOUNTING_BOT_TOKEN")
GSHEET_ID = os.getenv("BOT_GSHEET_ID")
GSHEET_CREDS_PATH = os.getenv("GSHEET_CREDS_PATH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Email Configuration
ACCOUNTING_EMAIL = os.getenv("ACCOUNTING_EMAIL")
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# Image Storage
IMAGE_DIR = os.path.join(os.getcwd(), "output", "payments")
os.makedirs(IMAGE_DIR, exist_ok=True)

# Configure Gemini Client
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Gemini Client: {e}")
else:
    logger.warning("GEMINI_API_KEY not found in environment variables.")

def authenticate_gspread():
    """Authenticate and return the Google Sheets client."""
    if not GSHEET_CREDS_PATH or not os.path.exists(GSHEET_CREDS_PATH):
        raise FileNotFoundError(f"Google Sheets credentials file not found at: {GSHEET_CREDS_PATH}")
    
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(GSHEET_CREDS_PATH, scope)
    return gspread.authorize(creds)

async def extract_data_from_image(image_bytes: bytes) -> Optional[dict]:
    """Use Gemini Vision to extract data from the bank slip."""
    prompt = """
    This is a Thai bank payment slip. Please extract the following details in JSON format:
    - sender_name: The name of the person who sent the money.
    - receiver_name: The name of the person or entity who received the money.
    - amount: The amount transferred as a number (remove commas).
    - currency: The currency (usually THB).
    - date: The date of the transaction (YYYY-MM-DD).
    - time: The time of the transaction (HH:MM:SS or HH:MM).
    - reference_no: The reference or transaction number.

    If any field is not found, use null.
    Return ONLY the JSON. No extra text, no markdown blocks.
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-flash-latest',
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type='image/jpeg'),
                prompt
            ]
        )
        
        text = response.text.strip()
        if text.startswith("```"):
            text = text.replace("```json", "").replace("```", "").strip()
        
        logger.info(f"Gemini raw response: {text}")
        return json.loads(text)
    except Exception as e:
        logger.error(f"Error in OCR (New SDK): {e}")
        return None

def send_accounting_email(data: dict, image_path: str):
    """Send an email notification to accounting."""
    if not all([ACCOUNTING_EMAIL, SMTP_SERVER, SMTP_USER, SMTP_PASSWORD]):
        logger.warning("Email configuration missing. Skipping email notification.")
        return False

    try:
        msg = EmailMessage()
        msg['Subject'] = f"QR payment : {data.get('time', 'unknown')}, {data.get('date', 'unknown')}"
        msg['From'] = SMTP_USER
        msg['To'] = ACCOUNTING_EMAIL

        body = (
            f"Hello my dear accounting,\n\n"
            f"Here is a new payment notification:\n\n"
            f"👤 Sender: {data.get('sender_name')}\n"
            f"🏢 Receiver: {data.get('receiver_name')}\n"
            f"💰 Amount: {data.get('amount')} {data.get('currency', 'THB')}\n"
            f"📅 Date/Time: {data.get('date')} {data.get('time')}\n"
            f"🔢 Reference: {data.get('reference_no')}\n\n"
            f"Please find the payment slip attached.\n\n"
            f"Sent by Payment Bot"
        )
        msg.set_content(body)

        # Attach image
        with open(image_path, 'rb') as f:
            file_data = f.read()
            msg.add_attachment(
                file_data,
                maintype='image',
                subtype='jpeg',
                filename=os.path.basename(image_path)
            )

        # Connect and send
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
            logger.info(f"Email sent to {ACCOUNTING_EMAIL}")
        return True
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return False

def update_gsheet(data: dict, image_path: str):
    """Append data to GSheet and sort by time."""
    try:
        gc = authenticate_gspread()
        sh = gc.open_by_key(GSHEET_ID)
        ws = sh.get_worksheet(0) # Use the first sheet

        # Prepare row
        th_tz = pytz.timezone('Asia/Bangkok')
        now_th = datetime.datetime.now(th_tz)
        timestamp = now_th.strftime("%Y-%m-%d %H:%M:%S")
        row = [
            data.get('date'),
            data.get('time'),
            data.get('sender_name'),
            data.get('receiver_name'),
            data.get('amount'),
            data.get('reference_no'),
            f"file://{image_path}", # Local for now, could be Drive link
            timestamp # Processed at
        ]

        # Append row
        ws.append_row(row)
        logger.info(f"Appended row: {row}")

        # Sorting logic
        all_values = ws.get_all_values()
        if len(all_values) > 1:
            header = all_values[0]
            rows = all_values[1:]
            df = pd.DataFrame(rows, columns=header)
            
            # Combine Date and Time for sorting
            df['datetime_sort'] = pd.to_datetime(df.iloc[:, 0] + ' ' + df.iloc[:, 1], errors='coerce')
            df = df.sort_values(by='datetime_sort', ascending=True)
            df = df.drop(columns=['datetime_sort'])
            
            # Update entire sheet
            ws.clear()
            ws.update([header] + df.values.tolist())
            logger.info("Sorted Google Sheet by time.")
        
        return True
    except Exception as e:
        logger.error(f"Error updating GSheet: {e}")
        logger.error(traceback.format_exc())
        return False

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming photo messages."""
    if not update.message or not update.message.photo:
        return

    await update.message.reply_text("Processing your payment slip... ⏳")

    try:
        # Get the largest photo
        photo_file = await update.message.photo[-1].get_file()
        file_bytearray = await photo_file.download_as_bytearray()
        image_bytes = bytes(file_bytearray)

        # Save image locally
        th_tz = pytz.timezone('Asia/Bangkok')
        now_th = datetime.datetime.now(th_tz)
        filename = f"payment_{now_th.strftime('%Y%m%d_%H%M%S')}_{update.message.from_user.id}.jpg"
        image_path = os.path.join(IMAGE_DIR, filename)
        with open(image_path, "wb") as f:
            f.write(image_bytes)
        logger.info(f"Image saved locally at {image_path}")

        # OCR
        data = await extract_data_from_image(image_bytes)
        if not data:
            await update.message.reply_text("❌ Failed to parse the bank slip. Please make sure the image is clear.")
            return

        # Notify user of extracted data
        summary = (
            f"✅ Data Extracted:\n"
            f"👤 Sender: {data.get('sender_name')}\n"
            f"🏢 Receiver: {data.get('receiver_name')}\n"
            f"💰 Amount: {data.get('amount')} {data.get('currency', 'THB')}\n"
            f"📅 Time: {data.get('date')} {data.get('time')}\n"
            f"🔢 Ref: {data.get('reference_no')}"
        )
        await update.message.reply_text(summary)

        # Update GSheet
        await update.message.reply_text("📊 Syncing with Google Sheets...")
        loop = asyncio.get_event_loop()
        gsheet_success = await loop.run_in_executor(None, update_gsheet, data, image_path)
        
        if gsheet_success:
            await update.message.reply_text("✅ Success! Data and link saved to Google Sheet.")
        else:
            await update.message.reply_text("⚠️ Warning: Data extracted but Google Sheet sync failed.")

        # Send Email
        await update.message.reply_text("📧 Sending notification to accounting...")
        email_success = await loop.run_in_executor(None, send_accounting_email, data, image_path)
        
        if email_success:
            await update.message.reply_text("📩 Email sent to accounting successfully.")
        else:
            await update.message.reply_text("⚠️ Warning: Could not send email. Check SMTP settings.")

    except Exception as e:
        logger.error(f"Error handling photo: {e}")
        await update.message.reply_text("❌ An unexpected error occurred while processing the image.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler."""
    await update.message.reply_text(
        "Hello! Send me a photo of a Thai bank payment slip, and I will transcribe it into your Google Sheet and notify accounting via email."
    )

if __name__ == '__main__':
    if not all([BOT_TOKEN, GSHEET_ID, GSHEET_CREDS_PATH, GEMINI_API_KEY]):
        print("CRITICAL ERROR: Missing environment variables. Please check your .env file.")
        print(f"BOT_TOKEN: {'SET' if BOT_TOKEN else 'MISSING'}")
        print(f"GSHEET_ID: {'SET' if GSHEET_ID else 'MISSING'}")
        print(f"GSHEET_CREDS_PATH: {'SET' if GSHEET_CREDS_PATH else 'MISSING'}")
        print(f"GEMINI_API_KEY: {'SET' if GEMINI_API_KEY else 'MISSING'}")
        exit(1)

    # Print service account email for debugging
    try:
        with open(GSHEET_CREDS_PATH, 'r') as f:
            creds_data = json.load(f)
            print(f"Service Account Email: {creds_data.get('client_email')}")
            print(f"Please ensure the Google Sheet is shared with this email as an 'Editor'.")
    except Exception as e:
        print(f"Warning: Could not read service account email from JSON: {e}")

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    start_handler = CommandHandler('start', start)
    photo_handler = MessageHandler(filters.PHOTO, handle_photo)
    
    application.add_handler(start_handler)
    application.add_handler(photo_handler)
    
    print("Bot is starting (Phase 2 enabled)...")
    application.run_polling()
