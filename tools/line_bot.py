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
from fastapi import FastAPI, Request, HTTPException
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
    ImageMessage,
    UserProfileResponse,
    QuickReply,
    QuickReplyItem,
    PostbackAction
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent, PostbackEvent

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
LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
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
IMAGE_DIR = os.path.join(os.getcwd(), "output", "payments_line")
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

# Initialize LINE API
configuration = Configuration(access_token=LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)
app = FastAPI()

# Initialize Prisma & Gemini
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Gemini Client: {e}")
else:
    logger.warning("GEMINI_API_KEY not found.")

db = Prisma()

# --- Translations ---
MESSAGES = {
    "en": {
        "welcome": "Welcome to **SlipSync**! 🚀\n\n1️⃣ Share your Google Sheet with **Editor** access to:\n`slipsync@googlegroups.com` \n(☝🏻 Tap to copy)\n\n2️⃣ Send me the Sheet URL to link it.\n\n3️⃣ Send a photo of a Thai bank slip to sync! 📊",
        "processing": "Processing your payment slip... ⏳",
        "ocr_failed": "❌ Failed to parse the bank slip. Please ensure the image is clear.",
        "success": "✅ Success! Data saved to Google Sheet.\n\n💰 Daily Total: {daily_total:,.2f} THB",
        "gsheet_linked": "✅ Google Sheet linked! ID: {id}\n\nMake sure you shared it with: `slipsync@googlegroups.com`",
        "gsheet_fail": "❌ Failed to link Google Sheet.",
        "link_instr": "Please share your sheet with `slipsync@googlegroups.com` and send the URL here first.",
        "status": "📊 **SlipSync Status**\nPlan: {plan}\nExpires: {expires}\nDaily: {count}/{limit}\nSheet: {sheet}",
        "upgrade": "Contact @autokoh to upgrade to Pro!",
        "daily_limit": "⚠️ Daily limit reached ({limit} slips). Try again tomorrow.",
        "trial_expired": "❌ Your trial has expired. Contact @autokoh to upgrade.",
        "undo_success": "✅ **Undo Successful!**\n\n- Removed from Google Sheet\n- Accounting notified\n- Daily Total updated",
        "undo_no_payment": "🧐 No recent payments found to undo.",
        "undo_fail": "❌ Failed to undo the last action."
    },
    "th": {
        "welcome": "ยินดีต้อนรับสู่ **SlipSync**! 🚀\n\n1️⃣ แชร์ Google Sheet ของคุณ (สิทธิ์ Editor) ให้ที่อีเมล:\n`slipsync@googlegroups.com` (แตะเพื่อคัดลอก)\n\n2️⃣ ส่ง URL ของ Sheet มาให้เราเพื่อเชื่อมต่อ\n\n3️⃣ ส่งรูปสลิปธนาคารเพื่อบันทึกข้อมูล! 📊",
        "processing": "กำลังประมวลผลสลิป... ⏳",
        "ocr_failed": "❌ ไม่สามารถอ่านข้อมูลสลิปได้ กรุณาส่งรูปที่ชัดเจนกว่าเดิม",
        "success": "✅ สำเร็จ! บันทึกข้อมูลลง Google Sheet เรียบร้อยแล้ว\n\n💰 ยอดรวมวันนี้: {daily_total:,.2f} THB",
        "gsheet_linked": "✅ เชื่อมต่อ Google Sheet เรียบร้อย! ID: {id}\n\nอย่าลืมแชร์สิทธิ์ Editor ให้: `slipsync@googlegroups.com`",
        "gsheet_fail": "❌ เชื่อมต่อ Google Sheet ไม่สำเร็จ",
        "link_instr": "กรุณาแชร์ชีตให้ `slipsync@googlegroups.com` และส่ง URL มาให้เราก่อนใช้งาน",
        "status": "📊 **สถานะ SlipSync**\nแพ็กเกจ: {plan}\nหมดอายุ: {expires}\nวันนี้: {count}/{limit}\nชีต: {sheet}",
        "upgrade": "ติดต่อ @autokoh เพื่ออัปเกรดเป็น Pro!",
        "daily_limit": "⚠️ ใช้งานครบจำนวนจำกัดต่อวันแล้ว ({limit} สลิป) กรุณาลองใหม่พรุ่งนี้",
        "trial_expired": "❌ ระยะเวลาทดลองใช้งานหมดแล้ว ติดต่อ @autokoh เพื่ออัปเกรด",
        "undo_success": "✅ **ยกเลิกสำเร็จ!**\n\n- ลบข้อมูลจาก Google Sheet แล้ว\n- แจ้งฝ่ายบัญชีแล้ว\n- อัปเดตยอดรวมวันนี้แล้ว",
        "undo_no_payment": "🧐 ไม่พบรายการล่าสุดที่สามารถยกเลิกได้",
        "undo_fail": "❌ ไม่สามารถยกเลิกรายการล่าสุดได้"
    },
    "my": {
        "welcome": "**SlipSync** မှ ကြိုဆိုပါတယ်! 🚀\n\n၁။ Google Sheet ကို Editor access ဖြင့် ဤအီးမေးလ်သို့ share ပေးပါ:\n`slipsync@googlegroups.com` (ကူးယူရန် နှိပ်ပါ)\n\n၂။ ချိတ်ဆက်ရန် Sheet URL ကို ပေးပို့ပါ။\n\n၃။ အချက်အလက်သိမ်းရန် ဘဏ်စလစ်ပုံကို ပေးပို့ပါ။ 📊",
        "processing": "စလစ်ကို စစ်ဆေးနေပါတယ်... ⏳",
        "ocr_failed": "❌ အချက်အလက်ဖတ်မရပါ။ ပုံကို ပိုမိုရှင်းလင်းစွာ ပြန်လည်ပေးပို့ပေးပါ။",
        "success": "✅ အောင်မြင်သည်။ Google Sheet ထဲသို့ အချက်အလက်များ သိမ်းဆည်းပြီးပါပြီ။\n\n💰 ယနေ့စုစုပေါင်း: {daily_total:,.2f} THB",
        "gsheet_linked": "✅ Google Sheet ချိတ်ဆက်ပြီးပါပြီ! ID: {id}\n\nဤအီးမေးလ်ကို share ရန် မမေ့ပါနှင့်: `slipsync@googlegroups.com`",
        "gsheet_fail": "❌ Google Sheet ချိတ်ဆက်မှု မအောင်မြင်ပါ။",
        "link_instr": "အသုံးမပြုမီ `slipsync@googlegroups.com` သို့ share ပြီး Sheet URL ကို အရင်ပေးပို့ပါ။",
        "status": "📊 **SlipSync အခြေအနေ**\nအမျိုးအစား: {plan}\nသက်တမ်းကုန်ရက်: {expires}\nယနေ့: {count}/{limit}\nSheet: {sheet}",
        "upgrade": "Pro သို့ အဆင့်မြှင့်ရန် @autokoh ကို ဆက်သွယ်ပါ။",
        "daily_limit": "⚠️ တစ်နေ့တာ ကန့်သတ်ချက် ပြည့်သွားပါပြီ ({limit} စောင်)။ မနက်ဖြန်မှ ပြန်ကြိုးစားပါ။",
        "trial_expired": "❌ စမ်းသပ်ကာလ ကုန်ဆုံးသွားပါပြီ။ အဆင့်မြှင့်ရန် @autokoh ကို ဆက်သွယ်ပါ။"
    }
}

async def get_user_language(user_id: str) -> str:
    """Detect user language from LINE profile."""
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            profile: UserProfileResponse = line_bot_api.get_profile(user_id)
            lang = profile.language or "en"
            if lang.startswith("th"): return "th"
            if lang.startswith("my"): return "my"
            return "en"
    except Exception:
        return "en"

def get_msg(key: str, lang: str, **kwargs) -> str:
    """Retrieve translated message."""
    text = MESSAGES.get(lang, MESSAGES["en"]).get(key, MESSAGES["en"][key])
    return text.format(**kwargs)

# --- Shared Logic ---

def authenticate_gspread():
    if not GSHEET_CREDS_PATH or not os.path.exists(GSHEET_CREDS_PATH):
        raise FileNotFoundError(f"Creds missing at {GSHEET_CREDS_PATH}")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(GSHEET_CREDS_PATH, scope)
    return gspread.authorize(creds)

async def extract_data_from_image(image_bytes: bytes) -> Optional[dict]:
    prompt = """
    This is a Thai bank payment slip. Extract in JSON:
    - sender_name, receiver_name, amount (number), currency (usually THB), date (YYYY-MM-DD), time (HH:MM), reference_no.
    Return ONLY JSON.
    """
    try:
        response = gemini_client.models.generate_content(
            model='gemini-flash-latest',
            contents=[types.Part.from_bytes(data=image_bytes, mime_type='image/jpeg'), prompt]
        )
        text = response.text.strip()
        if text.startswith("```"):
            text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        logger.error(f"OCR Error: {e}")
        return None

def update_gsheet(data: dict, image_path: str, target_gsheet_id: str = None):
    try:
        sheet_id = target_gsheet_id or GSHEET_ID
        if not sheet_id: return False
        gc = authenticate_gspread()
        sh = gc.open_by_key(sheet_id)
        ws = sh.get_worksheet(0)
        row = [data.get('date'), data.get('time'), data.get('sender_name'), data.get('receiver_name'), data.get('amount'), data.get('reference_no'), f"file://{image_path}", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
        ws.append_row(row)
        return True
    except Exception as e:
        logger.error(f"GSheet Error: {e}")
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
            if len(row) > 5 and row[5] == reference_no:
                ws.delete_rows(idx + 1)
                return True
        return False
    except Exception as e:
        logger.error(f"Error deleting row: {e}")
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
            f"⚠️ ATTENTION ACCOUNTING (LINE):\n\n"
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

async def get_or_create_sub(line_id: str):
    """Get or create subscription for LINE user."""
    if not db.is_connected():
        await db.connect()
    
    user = await db.authorizeduser.find_first(
        where={'platform_id': line_id, 'platform': 'line'},
        include={'subscription': True}
    )
    if user:
        return user.subscription
    
    # Create trial
    trial_expires = datetime.datetime.now() + datetime.timedelta(days=7)
    sub = await db.subscription.create(
        data={
            'trial_expires_at': trial_expires,
            'is_paid': False,
            'max_devices': 3,
            'rate_limit_daily': 10
        }
    )
    await db.authorizeduser.create(
        data={
            'platform_id': line_id,
            'platform': 'line',
            'subscription_id': sub.id
        }
    )
    return sub

async def check_usage_and_rate_limit(subscription, lang: str):
    """Check subscription validity (trial and daily limit)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    trial_expires = subscription.trial_expires_at
    if trial_expires.tzinfo is None:
         trial_expires = trial_expires.replace(tzinfo=datetime.timezone.utc)
    
    if not subscription.is_paid and now > trial_expires:
        return False, get_msg("trial_expired", lang)

    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    usage_count = await db.usagelog.count(
        where={
            'subscription_id': subscription.id,
            'used_at': {'gte': start_of_day}
        }
    )

    if usage_count >= subscription.rate_limit_daily:
        return False, get_msg("daily_limit", lang, limit=subscription.rate_limit_daily)

    return True, None

# --- FASTAPI Webhook ---

@app.post("/webhook")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    user_id = event.source.user_id
    text = event.message.text
    asyncio.run(process_text(user_id, text, event.reply_token))

async def process_text(user_id, text, reply_token):
    lang = await get_user_language(user_id)
    sub = await get_or_create_sub(user_id)
    
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', text)
    if match:
        gsheet_id = match.group(1)
        await db.subscription.update(
            where={'id': sub.id},
            data={'gsheet_id': gsheet_id}
        )
        reply = get_msg("gsheet_linked", lang, id=gsheet_id, email=SERVICE_ACCOUNT_EMAIL)
    elif "status" in text.lower():
        status_str = "Pro ✅" if sub.is_paid else "Free Trial 🎁"
        expires = sub.trial_expires_at.strftime("%Y-%m-%d")
        
        # Count usage today
        now = datetime.datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        usage_count = await db.usagelog.count(
            where={'subscription_id': sub.id, 'used_at': {'gte': start_of_day}}
        )
        
        reply = get_msg("status", lang, 
                        plan=status_str, 
                        expires=expires, 
                        count=usage_count, 
                        limit=sub.rate_limit_daily, 
                        sheet="✅" if sub.gsheet_id else "❌")
    elif "undo" in text.lower():
        # Find last payment
        last_payment = await db.payment.find_first(
            where={'subscription_id': sub.id},
            order={'created_at': 'desc'}
        )
        if not last_payment:
            reply = get_msg("undo_no_payment", lang)
        else:
            # Delete from GSheet
            gs_success = delete_row_from_gsheet(last_payment.reference_no, sub.gsheet_id)
            
            # Send Email
            email_data = {
                'amount': last_payment.amount,
                'currency': last_payment.currency,
                'reference_no': last_payment.reference_no,
                'sender_name': last_payment.sender_name,
                'time': last_payment.created_at.strftime("%H:%M:%S"),
                'date': last_payment.created_at.strftime("%Y-%m-%d")
            }
            send_cancellation_email(email_data)
            
            # Delete from DB
            await db.payment.delete(where={'id': last_payment.id})
            reply = get_msg("undo_success", lang)
    else:
        reply = get_msg("welcome", lang, email=SERVICE_ACCOUNT_EMAIL)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=reply)]
        ))

@handler.add(PostbackEvent)
def handle_postback(event):
    user_id = event.source.user_id
    data = event.postback.data
    reply_token = event.reply_token
    asyncio.run(process_postback(user_id, data, reply_token))

async def process_postback(user_id, data, reply_token):
    if data == 'undo_last':
        lang = await get_user_language(user_id)
        sub = await get_or_create_sub(user_id)
        
        last_payment = await db.payment.find_first(
            where={'subscription_id': sub.id},
            order={'created_at': 'desc'}
        )
        
        if not last_payment:
            reply = get_msg("undo_no_payment", lang)
        else:
            # Execute Undo
            gs_success = delete_row_from_gsheet(last_payment.reference_no, sub.gsheet_id)
            email_data = {
                'amount': last_payment.amount,
                'currency': last_payment.currency,
                'reference_no': last_payment.reference_no,
                'sender_name': last_payment.sender_name,
                'time': last_payment.created_at.strftime("%H:%M:%S"),
                'date': last_payment.created_at.strftime("%Y-%m-%d")
            }
            send_cancellation_email(email_data)
            await db.payment.delete(where={'id': last_payment.id})
            
            # Message update with new total
            th_tz = pytz.timezone('Asia/Bangkok')
            now_th = datetime.datetime.now(th_tz)
            start_of_day_th = now_th.replace(hour=0, minute=0, second=0, microsecond=0)
            payments = await db.payment.find_many(
                where={'subscription_id': sub.id, 'created_at': {'gte': start_of_day_th}}
            )
            new_total = sum(p.amount for p in payments)
            
            reply = get_msg("undo_success", lang) + f"\n\n💰 Daily Total: {new_total:,.2f} THB"

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=reply)]
            ))

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    user_id = event.source.user_id
    msg_id = event.message.id
    reply_token = event.reply_token
    asyncio.run(process_image(user_id, msg_id, reply_token))

async def process_image(user_id, msg_id, reply_token):
    lang = await get_user_language(user_id)
    sub = await get_or_create_sub(user_id)
    
    # Check limit
    is_allowed, reason = await check_usage_and_rate_limit(sub, lang)
    if not is_allowed:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=reason)]
            ))
        return

    # Show loading animation
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        try:
            line_bot_api.show_loading_animation(user_id, 30) # 30 seconds max
        except Exception as e:
            logger.warning(f"Failed to show loading animation: {e}")

    # Image download & save
    with ApiClient(configuration) as api_client:
        api_blob = MessagingApiBlob(api_client)
        content = api_blob.get_message_content(msg_id)
        image_bytes = bytes(content)

    filename = f"line_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{user_id}.jpg"
    image_path = os.path.join(IMAGE_DIR, filename)
    with open(image_path, "wb") as f:
        f.write(image_bytes)

    # OCR & GSheet
    data = await extract_data_from_image(image_bytes)
    if not data:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=get_msg("ocr_failed", lang))]
            ))
        return

    # Log usage
    await db.usagelog.create(data={'subscription_id': sub.id, 'platform': 'line'})

    # Save Payment for daily sum tracking
    try:
        amount_val = 0.0
        if data.get('amount'):
            amount_str = str(data.get('amount')).replace(',', '')
            amount_val = float(amount_str)
        
        await db.payment.create(
            data={
                'subscription_id': sub.id,
                'amount': amount_val,
                'currency': data.get('currency', 'THB'),
                'sender_name': data.get('sender_name'),
                'reference_no': data.get('reference_no'),
                'platform': 'line'
            }
        )
    except Exception as e:
        logger.error(f"Failed to save payment record (LINE): {e}")

    # Push summary (using reply token for the first message)
    summary = f"💰 {data.get('amount')} {data.get('currency')}\n👤 {data.get('sender_name')}\n📅 {data.get('date')} {data.get('time')}"
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        # We reply with the summary
        line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=summary)]
        ))
        
        # Then we push updates (since we can only reply once)
        # Update GSheet
        success = update_gsheet(data, image_path, sub.gsheet_id)
        if success:
            # Calculate Daily sum
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
            
            # Send Success Message with Quick Reply Undo
            quick_reply = QuickReply(items=[
                QuickReplyItem(action=PostbackAction(label="Undo ↩️", data="undo_last", display_text="Undo Last Action"))
            ])
            
            msg_text = get_msg("success", lang, daily_total=daily_sum)
            line_bot_api.push_message(user_id, TextMessage(text=msg_text, quick_reply=quick_reply))
        else:
            line_bot_api.push_message(user_id, TextMessage(text=get_msg("link_instr", lang)))

if __name__ == "__main__":
    import uvicorn
    # Make sure to connect DB
    asyncio.run(db.connect())
    uvicorn.run(app, host="0.0.0.0", port=8000)
