import os
import io
import logging
import datetime
import json
import asyncio
import traceback
import smtplib
import re
from email.message import EmailMessage
import pytz
from typing import Optional

import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

from google import genai
from google.genai import types
from telegram import Update, File, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler, CallbackQueryHandler, PreCheckoutQueryHandler

from prisma import Prisma

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
BOT_TOKEN = os.getenv("SLIPSYNC_BOT_TOKEN")
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

# Service Account Email for instructions
SERVICE_ACCOUNT_EMAIL = None
if GSHEET_CREDS_PATH and os.path.exists(GSHEET_CREDS_PATH):
    try:
        with open(GSHEET_CREDS_PATH, 'r') as f:
            creds_data = json.load(f)
            SERVICE_ACCOUNT_EMAIL = creds_data.get('client_email')
    except Exception as e:
        logger.warning(f"Could not load service account email: {e}")

# Payment Constants
PROMPTPAY_RECEIVER_NAME = os.getenv("PROMPTPAY_RECEIVER_NAME", "YOUR NAME HERE")
STARS_PRICE = int(os.getenv("STARS_PRICE", 500)) # Default 500 Stars for Pro

# Configure Gemini Client
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Gemini Client: {e}")
else:
    logger.warning("GEMINI_API_KEY not found in environment variables.")

# Initialize Prisma
db = Prisma()

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
            f"Sent by SlipSync Bot"
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

def send_cancellation_email(data: dict):
    """Send a cancellation notice to accounting."""
    if not all([ACCOUNTING_EMAIL, SMTP_SERVER, SMTP_USER, SMTP_PASSWORD]):
        return False
    try:
        msg = EmailMessage()
        msg['Subject'] = f"RESCINDED: QR payment : {data.get('time', 'unknown')}, {data.get('date', 'unknown')}"
        msg['From'] = SMTP_USER
        msg['To'] = ACCOUNTING_EMAIL

        body = (
            f"⚠️ ATTENTION ACCOUNTING:\n\n"
            f"The following payment has been DELETED/CANCELLED by the user:\n\n"
            f"👤 Sender: {data.get('sender_name')}\n"
            f"💰 Amount: {data.get('amount')} {data.get('currency', 'THB')}\n"
            f"🔢 Reference: {data.get('reference_no')}\n\n"
            f"Please ignore the previous notification for this transaction."
        )
        msg.set_content(body)
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error(f"Error sending cancellation email: {e}")
        return False

def update_gsheet(data: dict, image_path: str, target_gsheet_id: str = None):
    """Append data to GSheet and sort by time."""
    try:
        sheet_id = target_gsheet_id or GSHEET_ID
        if not sheet_id:
            logger.error("No Google Sheet ID provided.")
            return False

        gc = authenticate_gspread()
        sh = gc.open_by_key(sheet_id)
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

def delete_row_from_gsheet(reference_no: str, target_gsheet_id: str):
    """Delete a row from GSheet based on reference number."""
    try:
        if not target_gsheet_id: return False
        gc = authenticate_gspread()
        sh = gc.open_by_key(target_gsheet_id)
        ws = sh.get_worksheet(0)
        
        all_values = ws.get_all_values()
        for idx, row in enumerate(all_values):
            # Check the reference number column (index 5)
            if len(row) > 5 and row[5] == reference_no:
                ws.delete_rows(idx + 1)
                logger.info(f"Deleted GSheet row {idx+1} with ref {reference_no}")
                return True
        logger.warning(f"Could not find row with ref {reference_no}")
        return False
    except Exception as e:
        logger.error(f"Error deleting GSheet row: {e}")
        return False

async def get_or_create_subscription(telegram_id: int):
    """Get subscription for user or create a new trial."""
    platform_id = str(telegram_id)
    user = await db.authorizeduser.find_first(
        where={'platform_id': platform_id, 'platform': 'telegram'}, 
        include={'subscription': True}
    )
    if user:
        return user.subscription
    
    # Create new trial subscription
    trial_expires_at = datetime.datetime.now() + datetime.timedelta(days=7)
    sub = await db.subscription.create(
        data={
            'trial_expires_at': trial_expires_at,
            'is_paid': False,
            'max_devices': 3,
            'rate_limit_daily': 10
        }
    )
    await db.authorizeduser.create(
        data={
            'platform_id': platform_id,
            'platform': 'telegram',
            'subscription_id': sub.id
        }
    )
    return sub

async def check_usage_and_rate_limit(subscription):
    """Check if the subscription is still valid and within rate limits."""
    # Use UTC to match Prisma's stored datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    
    # Ensure trial_expires_at is comparable
    trial_expires = subscription.trial_expires_at
    if trial_expires.tzinfo is None:
         trial_expires = trial_expires.replace(tzinfo=datetime.timezone.utc)
    
    if not subscription.is_paid and now > trial_expires:
        return False, "❌ Your trial has expired. Please contact the owner @autokoh to upgrade."

    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    usage_count = await db.usagelog.count(
        where={
            'subscription_id': subscription.id,
            'used_at': {'gte': start_of_day}
        }
    )

    if usage_count >= subscription.rate_limit_daily:
        return False, f"⚠️ Daily limit reached ({subscription.rate_limit_daily} slips). Try again tomorrow or upgrade by contacting @autokoh."

    return True, None

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming photo messages."""
    if not update.message or not update.message.photo:
        return

    telegram_id = update.message.from_user.id
    
    try:
        if not db.is_connected():
            await db.connect()
        
        sub = await get_or_create_subscription(telegram_id)
        is_allowed, reason = await check_usage_and_rate_limit(sub)
        
        if not is_allowed:
            await update.message.reply_text(reason)
            return

        await update.message.reply_text("Processing your payment slip... ⏳")
        # Send typing action
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

        # Get the largest photo
        photo_file = await update.message.photo[-1].get_file()
        file_bytearray = await photo_file.download_as_bytearray()
        image_bytes = bytes(file_bytearray)

        # Save image locally
        th_tz = pytz.timezone('Asia/Bangkok')
        now_th = datetime.datetime.now(th_tz)
        filename = f"payment_{now_th.strftime('%Y%m%d_%H%M%S')}_{telegram_id}.jpg"
        image_path = os.path.join(IMAGE_DIR, filename)
        with open(image_path, "wb") as f:
            f.write(image_bytes)
        logger.info(f"Image saved locally at {image_path}")

        # OCR
        data = await extract_data_from_image(image_bytes)
        if not data:
            await update.message.reply_text("❌ Failed to parse the bank slip. Please make sure the image is clear.")
            return

        # Check for Auto-Upgrade (PromptPay OCR)
        if not sub.is_paid and PROMPTPAY_RECEIVER_NAME != "YOUR NAME HERE":
            extracted_receiver = str(data.get('receiver_name', '')).upper()
            if PROMPTPAY_RECEIVER_NAME.upper() in extracted_receiver:
                await db.subscription.update(
                    where={'id': sub.id},
                    data={'is_paid': True, 'rate_limit_daily': 1000} # Upgrade to Pro
                )
                sub.is_paid = True
                await update.message.reply_text("🎊 **PRO UPGRADE DETECTED!** 🎊\n\nThank you for your payment! Your account has been upgraded to SlipSync Pro automatically. ✅")
                logger.info(f"Auto-upgraded subscription {sub.id} via OCR match.")

        # Log usage
        await db.usagelog.create(data={'subscription_id': sub.id, 'platform': 'telegram'})
        
        # Save Payment for daily sum tracking
        try:
            amount_val = 0.0
            if data.get('amount'):
                # Extract numeric value from amount string (remove commas etc)
                amount_str = str(data.get('amount')).replace(',', '')
                amount_val = float(amount_str)
            
            await db.payment.create(
                data={
                    'subscription_id': sub.id,
                    'amount': amount_val,
                    'currency': data.get('currency', 'THB'),
                    'sender_name': data.get('sender_name'),
                    'reference_no': data.get('reference_no'),
                    'platform': 'telegram'
                }
            )
            logger.info(f"Saved payment: {amount_val} for subscription {sub.id}")
        except Exception as e:
            logger.error(f"Failed to save payment record: {e}")

        # Notify user of extracted data
        summary = (
            f"✅ **Data Extracted**\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👤 Sender: {data.get('sender_name')}\n"
            f"🏢 Receiver: {data.get('receiver_name')}\n"
            f"💰 Amount: {data.get('amount')} {data.get('currency', 'THB')}\n"
            f"📅 Time: {data.get('date')} {data.get('time')}\n"
            f"🔢 Ref: {data.get('reference_no')}"
        )
        await update.message.reply_text(summary)

        # Update GSheet
        await update.message.reply_text("📊 Syncing with Google Sheets...")
        target_gsheet_id = sub.gsheet_id
        
        loop = asyncio.get_event_loop()
        gsheet_success = await loop.run_in_executor(None, update_gsheet, data, image_path, target_gsheet_id)
        
        if gsheet_success:
            # Calculate updated Daily Sum
            th_tz = pytz.timezone('Asia/Bangkok')
            now_th = datetime.datetime.now(th_tz)
            start_of_day_th = now_th.replace(hour=0, minute=0, second=0, microsecond=0)
            
            payments = await db.payment.find_many(
                where={
                    'subscription_id': sub.id,
                    'created_at': {'gte': start_of_day_th}
                }
            )
            daily_sum = sum(p.amount for p in payments)
            
            # Create Inline Keyboard for Undo
            keyboard = [[InlineKeyboardButton("Undo Last Action ↩️", callback_data='undo_last')]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"✅ Success! Data and link saved to Google Sheet.\n\n"
                f"💰 **Daily Total: {daily_sum:,.2f} THB**",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            # Note: query is not defined in handle_photo, commenting out for now
            # await query.answer(
            #     text=f"💰 **Daily Total: {daily_sum:,.2f} THB**",
            #     show_alert=True
            # )
        else:
            await update.message.reply_text("⚠️ Warning: Data extracted but Google Sheet sync failed.")

        # Send Email (Paid tier)
        if sub.is_paid:
            await update.message.reply_text("📧 Sending notification to accounting...")
            email_success = await loop.run_in_executor(None, send_accounting_email, data, image_path)
            
            if email_success:
                await update.message.reply_text("📩 Email sent to accounting successfully.")
            else:
                await update.message.reply_text("⚠️ Warning: Could not send email. Check SMTP settings.")
        else:
            await update.message.reply_text("💡 Upgrade to **SlipSync Pro** to enable Email, Cashier and Custom integrations! Contact @autokoh for details.")

    except Exception as e:
        logger.error(f"Error handling photo: {e}")
        await update.message.reply_text("❌ An unexpected error occurred while processing the image.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages (e.g., GSheet URLs)."""
    text = update.message.text
    if not text: return
    telegram_id = update.message.from_user.id
    
    # Regex for GSheet URL
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', text)
    if match:
        gsheet_id = match.group(1)
        try:
            if not db.is_connected():
                await db.connect()
            sub = await get_or_create_subscription(telegram_id)
            await db.subscription.update(
                where={'id': sub.id},
                data={'gsheet_id': gsheet_id}
            )
            await update.message.reply_text(f"✅ Google Sheet linked! ID: `{gsheet_id}`\n\nMake sure you have shared the sheet with Editor access to:\n`slipsync@googlegroups.com`", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error linking GSheet: {e}")
            await update.message.reply_text("❌ Failed to link Google Sheet.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler."""
    msg = (
        "Welcome to **SlipSync**! 🚀\n\n"
        "To automate your payment tracking:\n\n"
        "1️⃣ **Share your Google Sheet** with **Editor** access to:\n"
        "`slipsync@googlegroups.com` \n(☝🏻 Tap to copy)\n\n"
        "2️⃣ **Send me the URL** of your Google Sheet.\n\n"
        "3️⃣ **Send a photo** of a Thai bank slip.\n\n"
        "I will extract the details and sync them to your sheet instantly! 📊\n\n"
        "🎁 **Free Trial**: 1 week free (max 10 slips/day).\n"
        "🚀 **Go Pro**: Use /upgrade to unlock Email and unlimited slips!\n"
        "Use /status to check your plan."
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Upgrade command handler."""
    msg = (
        "🚀 **Upgrade to SlipSync Pro**\n\n"
        "Unlock premium features:\n"
        "✅ **Auto-Email** to Accounting\n"
        "✅ **Unlimited** Slips (No daily limit)\n"
        "✅ **Custom** Integrations\n\n"
        "Choose your payment method:"
    )
    keyboard = [
        [InlineKeyboardButton(f"Pay with Telegram Stars (⭐️ {STARS_PRICE})", callback_data='pay_stars')],
        [InlineKeyboardButton("Pay with PromptPay (QR Code)", callback_data='pay_promptpay')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pre-checkout query."""
    query = update.pre_checkout_query
    # Check if invoice payload is correct
    if query.invoice_payload != 'pro_upgrade_stars':
        await query.answer(ok=False, error_message="Something went wrong...")
    else:
        await query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle successful payment."""
    telegram_id = update.message.from_user.id
    try:
        if not db.is_connected():
            await db.connect()
        sub = await get_or_create_subscription(telegram_id)
        await db.subscription.update(
            where={'id': sub.id},
            data={'is_paid': True, 'rate_limit_daily': 1000}
        )
        await update.message.reply_text(
            "🎊 **Payment Successful!** 🎊\n\nWelcome to **SlipSync Pro**. Your account has been upgraded! 🚀",
            parse_mode='Markdown'
        )
        logger.info(f"Upgraded subscription {sub.id} via Telegram Stars.")
    except Exception as e:
        logger.error(f"Error in successful selection: {e}")
        await update.message.reply_text("❌ Upgrade successful but database update failed. Please contact @autokoh.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Status command handler."""
    telegram_id = update.message.from_user.id
    try:
        if not db.is_connected():
            await db.connect()
        sub = await get_or_create_subscription(telegram_id)
        
        status_str = "Pro ✅" if sub.is_paid else "Free Trial 🎁"
        expires = sub.trial_expires_at.strftime("%Y-%m-%d")
        
        # Calculate Daily Sum (Asia/Bangkok)
        th_tz = pytz.timezone('Asia/Bangkok')
        now_th = datetime.datetime.now(th_tz)
        start_of_day_th = now_th.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Convert start_of_day_th back to UTC for Prisma query if needed, 
        # but Prisma usually handles it if we pass aware datetime.
        payments = await db.payment.find_many(
            where={
                'subscription_id': sub.id,
                'created_at': {'gte': start_of_day_th}
            }
        )
        daily_sum = sum(p.amount for p in payments)
        
        msg = (
            f"📊 **SlipSync Status**\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👤 Plan: {status_str}\n"
            f"💰 **Daily Total: {daily_sum:,.2f} THB**\n"
            f"📅 Trial Expires: {expires}\n"
            f"📱 Device Limit: {sub.max_devices}\n"
            f"🔢 Daily Limit: {sub.rate_limit_daily}\n"
            f"📂 Linked Sheet: {'✅' if sub.gsheet_id else '❌ (Send URL to link)'}\n\n"
            f"Contact @autokoh to upgrade your plan and connect it to your own system."
        )
        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in status command: {e}")
        await update.message.reply_text("❌ Could not retrieve status.")

async def link_device(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to link another device/user to a subscription."""
    if not context.args:
        await update.message.reply_text("Usage: /link <subscription_id>")
        return
    
    sub_id = context.args[0]
    telegram_id = update.message.from_user.id
    
    try:
        if not db.is_connected():
            await db.connect()
        
        sub = await db.subscription.find_unique(where={'id': sub_id}, include={'users': True})
        if not sub:
            await update.message.reply_text("❌ Invalid Subscription ID.")
            return
        
        if len(sub.users) >= sub.max_devices:
            await update.message.reply_text(f"❌ Device limit reached ({sub.max_devices}).")
            return
        
        await db.authorizeduser.create(
            data={
                'platform_id': str(telegram_id), 
                'platform': 'telegram', 
                'subscription_id': sub_id
            }
        )
        await update.message.reply_text("✅ Device successfully linked to subscription!")
    except Exception as e:
        logger.error(f"Error linking device: {e}")
        await update.message.reply_text("❌ Failed to link device.")

async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Undo the last payment action."""
    telegram_id = update.message.from_user.id
    try:
        if not db.is_connected():
            await db.connect()
        
        sub = await get_or_create_subscription(telegram_id)
        
        # Find last payment for this subscription
        last_payment = await db.payment.find_first(
            where={'subscription_id': sub.id},
            order={'created_at': 'desc'}
        )
        
        if not last_payment:
            await update.message.reply_text("🧐 No recent payments found to undo.")
            return

        await update.message.reply_text(f"⏳ Undoing last payment of {last_payment.amount} {last_payment.currency}...")

        # 1. Delete from GSheet
        loop = asyncio.get_event_loop()
        gs_success = await loop.run_in_executor(None, delete_row_from_gsheet, last_payment.reference_no, sub.gsheet_id)
        
        # 2. Send Cancellation Email
        email_data = {
            'amount': last_payment.amount,
            'currency': last_payment.currency,
            'reference_no': last_payment.reference_no,
            'sender_name': last_payment.sender_name,
            'time': last_payment.created_at.strftime("%H:%M:%S"),
            'date': last_payment.created_at.strftime("%Y-%m-%d")
        }
        await loop.run_in_executor(None, send_cancellation_email, email_data)

        # 3. Delete from DB
        await db.payment.delete(where={'id': last_payment.id})

        reply = f"✅ **Undo Successful!**\n\n- Removed from Google Sheet\n- Accounting notified\n- Daily Total updated"
        await update.message.reply_text(reply, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in undo command: {e}")
        await update.message.reply_text("❌ An error occurred while trying to undo.")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries from inline buttons."""
    query = update.callback_query
    # Don't answer early - we want to show the alert later

    if query.data == 'undo_last':
        telegram_id = query.from_user.id
        try:
            if not db.is_connected():
                await db.connect()
            
            sub = await get_or_create_subscription(telegram_id)
            last_payment = await db.payment.find_first(
                where={'subscription_id': sub.id},
                order={'created_at': 'desc'}
            )
            
            if not last_payment:
                await query.answer(text="🧐 No recent payments found to undo.", show_alert=True)
                return

            # Execute Undo
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, delete_row_from_gsheet, last_payment.reference_no, sub.gsheet_id)
            
            email_data = {
                'amount': last_payment.amount,
                'currency': last_payment.currency,
                'reference_no': last_payment.reference_no,
                'sender_name': last_payment.sender_name,
                'time': last_payment.created_at.strftime("%H:%M:%S"),
                'date': last_payment.created_at.strftime("%Y-%m-%d")
            }
            await loop.run_in_executor(None, send_cancellation_email, email_data)
            await db.payment.delete(where={'id': last_payment.id})

            # Calculate NEW Daily Total for the toast
            th_tz = pytz.timezone('Asia/Bangkok')
            now_th = datetime.datetime.now(th_tz)
            start_of_day_th = now_th.replace(hour=0, minute=0, second=0, microsecond=0)
            payments = await db.payment.find_many(
                where={'subscription_id': sub.id, 'created_at': {'gte': start_of_day_th}}
            )
            new_total = sum(p.amount for p in payments)

            # Show Alert Notification (Popup)
            await query.answer(
                text=f"✅ Undo Success!\nNew Total: {new_total:,.2f} THB",
                show_alert=True
            )

            # Update the original message to reflect it was undone
            await query.edit_message_text(
                text=f"~~{query.message.text}~~\n\n↩️ **Transaction Undone**",
                parse_mode='Markdown'
            )

        except Exception as e:
            logger.error(f"Error in callback undo: {e}")
            await query.answer(text="❌ Failed to undo.")

    elif query.data == 'pay_stars':
        # Send Invoice for Stars
        title = "SlipSync Pro Upgrade"
        description = "One-time upgrade to unlock all premium features."
        payload = "pro_upgrade_stars"
        currency = "XTR"
        price = STARS_PRICE
        prices = [types.LabeledPrice("Pro Upgrade", price)]

        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=title,
            description=description,
            payload=payload,
            provider_token="", # Empty for Telegram Stars
            currency=currency,
            prices=prices
        )
        await query.answer()

    elif query.data == 'pay_promptpay':
        msg = (
            "💰 **PromptPay Payment**\n\n"
            "1️⃣ Transfer to this PromptPay account (Owner):\n"
            "`081-XXX-XXXX` (Placeholder)\n\n"
            "2️⃣ **Send the payment slip (photo)** directly to this bot.\n\n"
            "✨ The bot will use OCR to verify your payment and **upgrade you instantly!**"
        )
        await query.edit_message_text(msg, parse_mode='Markdown')
        await query.answer()

    elif query.data == 'check_total':
        telegram_id = query.from_user.id
        try:
            if not db.is_connected():
                await db.connect()
            
            sub = await get_or_create_subscription(telegram_id)
            
            # Calculate Daily Sum
            th_tz = pytz.timezone('Asia/Bangkok')
            now_th = datetime.datetime.now(th_tz)
            start_of_day_th = now_th.replace(hour=0, minute=0, second=0, microsecond=0)
            
            payments = await db.payment.find_many(
                where={
                    'subscription_id': sub.id,
                    'created_at': {'gte': start_of_day_th}
                }
            )
            daily_sum = sum(p.amount for p in payments)

            # Show Toast Notification (Top of screen)
            await query.answer(
                text=f"💰 Daily Total: {daily_sum:,.2f} THB",
                show_alert=True 
            )
        except Exception as e:
            logger.error(f"Error in check_total callback: {e}")
            await query.answer(text="❌ Failed to get total.")

if __name__ == '__main__':
    if not all([BOT_TOKEN, GSHEET_ID, GSHEET_CREDS_PATH, GEMINI_API_KEY]):
        print("CRITICAL ERROR: Missing environment variables. Please check your .env file.")
        exit(1)

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('status', status))
    application.add_handler(CommandHandler('upgrade', upgrade))
    application.add_handler(CommandHandler('link', link_device))
    application.add_handler(CommandHandler('undo', undo))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    
    print("SlipSync Bot is starting...")
    application.run_polling()
