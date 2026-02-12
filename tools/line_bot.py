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
from contextlib import asynccontextmanager

import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

from google import genai
from google.genai import types
from fastapi import FastAPI, Request, HTTPException
from linebot.v3.webhook import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    AsyncApiClient,
    AsyncMessagingApi,
    AsyncMessagingApiBlob,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    ImageMessage,
    UserProfileResponse,
    QuickReply,
    QuickReplyItem,
    PostbackAction,
    URIAction,
    FlexMessage,
    FlexContainer
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
SERVER_BASE_URL = os.getenv("SERVER_BASE_URL", "http://localhost:8000")  # ngrok URL or server URL

# Payment Constants
PROMPTPAY_RECEIVER_NAME = os.getenv("PROMPTPAY_RECEIVER_NAME", "YOUR NAME HERE")

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
parser = WebhookParser(LINE_SECRET)

# Initialize Prisma
db = Prisma()

def generate_invite_code(length=6):
    """Generate a short, readable invite code."""
    import random
    import string
    chars = string.ascii_uppercase + string.digits
    # Remove confusing characters (O, 0, I, 1, L)
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '').replace('L', '')
    return ''.join(random.choice(chars) for _ in range(length))

# FastAPI Lifespan for DB connection
@asynccontextmanager
async def lifespan(app):
    await db.connect()
    logger.info("Prisma connected.")
    yield
    await db.disconnect()
    logger.info("Prisma disconnected.")

app = FastAPI(lifespan=lifespan)

# Mount static files for serving slip images
from starlette.staticfiles import StaticFiles
app.mount("/images", StaticFiles(directory=IMAGE_DIR), name="images")

# Initialize Gemini
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Gemini Client: {e}")
else:
    logger.warning("GEMINI_API_KEY not found.")

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
        "undo_fail": "❌ Failed to undo the last action.",
        "upgrade_info": "🚀 **Go Pro**\n\n1️⃣ Transfer to PromptPay:\n`081-XXX-XXXX` (Placeholder)\n\n2️⃣ Send the slip here.\n\n✨ Bot will upgrade you automatically!",
        "pro_upgrade_success": "🎊 **PRO UPGRADE DETECTED!** 🎊\n\nThank you! Your account is now Pro. ✅",
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
        "upgrade": "🚀 อัปเกรดเป็น Pro!\n1️⃣ โอน PromptPay: `081-XXX-XXXX` \n2️⃣ ส่งสลิปมาที่นี่\n✨ ระบบจะอัปเกรดให้อัตโนมัติ",
        "pro_upgrade_success": "🎊 **อัปเกรดเป็น PRO สำเร็จ!** 🎊\n\nขอบคุณสำหรับการสนับสนุน! บัญชีของคุณเป็น Pro แล้ว ✅",
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

def create_payment_flex_message(data: dict, daily_total: float, slips_count: int = 0, gsheet_id: str = None) -> dict:
    """Create a beautiful Flex Message bubble for the payment summary."""
    amount_str = f"{data.get('amount', 0):,.2f}"
    currency = data.get('currency', 'THB')
    
    footer_contents = [
        {
            "type": "text",
            "text": "Synced to Google Sheet ✅",
            "size": "xs",
            "color": "#aaaaaa",
            "align": "center"
        }
    ]
    
    if gsheet_id:
        footer_contents.insert(0, {
            "type": "button",
            "action": {
                "type": "uri",
                "label": "📊 Open Google Sheet",
                "uri": f"https://docs.google.com/spreadsheets/d/{gsheet_id}"
            },
            "style": "primary",
            "color": "#1DB446",
            "margin": "md",
            "height": "sm"
        })

    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "PAYMENT SUCCESS",
                    "weight": "bold",
                    "color": "#1DB446",
                    "size": "sm"
                }
            ]
        },
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": f"฿ {amount_str}",
                    "size": "3xl",
                    "weight": "bold",
                    "color": "#111111",
                    "align": "center",
                    "margin": "md"
                },
                {
                    "type": "text",
                    "text": currency,
                    "size": "sm",
                    "color": "#aaaaaa",
                    "align": "center"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "separator",
                    "margin": "md"
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "contents": [
                        {"type": "text", "text": "Sender", "size": "sm", "color": "#555555", "flex": 0},
                        {"type": "text", "text": str(data.get('sender_name', '-')), "size": "sm", "color": "#111111", "align": "end", "wrap": True}
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "contents": [
                        {"type": "text", "text": "Receiver", "size": "sm", "color": "#555555", "flex": 0},
                        {"type": "text", "text": str(data.get('receiver_name', '-')), "size": "sm", "color": "#111111", "align": "end", "wrap": True}
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "contents": [
                        {"type": "text", "text": "Date", "size": "sm", "color": "#555555", "flex": 0},
                        {"type": "text", "text": f"{data.get('date')} {data.get('time')}", "size": "sm", "color": "#111111", "align": "end"}
                    ]
                },
                {
                    "type": "separator",
                    "margin": "xl"
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "contents": [
                        {"type": "text", "text": "Daily QR Total", "weight": "bold", "size": "md", "color": "#111111"},
                        {"type": "text", "text": f"💰 ฿ {daily_total:,.2f}", "weight": "bold", "size": "md", "color": "#111111", "align": "end"}
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "sm",
                    "contents": [
                        {"type": "text", "text": "Today's Slips", "size": "sm", "color": "#555555"},
                        {"type": "text", "text": f"📋 {slips_count} processed", "size": "sm", "color": "#1DB446", "align": "end"}
                    ]
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": footer_contents
        },
        "styles": {
            "header": {"backgroundColor": "#f8f9fa"},
            "footer": {"separator": True}
        }
    }

def create_undo_flex_message(daily_total: float, lang: str, gsheet_id: str = None) -> dict:
    """Create a Flex Message for Undo confirmation."""
    labels = {
        "en": "UNDO SUCCESSFUL",
        "th": "ยกเลิกรายการสำเร็จ",
        "my": "ပယ်ဖျက်ခြင်း အောင်မြင်သည်"
    }
    details = {
        "en": "- Removed from Google Sheet\n- Accounting notified\n- Daily Total updated",
        "th": "- ลบข้อมูลจาก Google Sheet แล้ว\n- แจ้งฝ่ายบัญชีแล้ว\n- อัปเดตยอดรวมรายวันแล้ว",
        "my": "- Google Sheet မှ ဖျက်လိုက်ပါပြီ\n- စာရင်းကိုင်ကို အကြောင်းကြားပြီးပါပြီ\n- စုစုပေါင်းကို အပ်ဒိတ်လုပ်ပြီးပါပြီ"
    }
    
    footer_contents = []
    if gsheet_id:
        footer_contents.append({
            "type": "button",
            "action": {
                "type": "uri",
                "label": "📊 Open Google Sheet",
                "uri": f"https://docs.google.com/spreadsheets/d/{gsheet_id}"
            },
            "style": "secondary",
            "height": "sm"
        })

    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": labels.get(lang, labels["en"]),
                    "weight": "bold",
                    "color": "#EB4E3D",
                    "size": "sm"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": details.get(lang, details["en"]),
                    "size": "sm",
                    "color": "#555555",
                    "wrap": True
                },
                {
                    "type": "separator",
                    "margin": "lg"
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "contents": [
                        {"type": "text", "text": "Daily QR Total", "weight": "bold", "size": "md", "color": "#111111"},
                        {"type": "text", "text": f"💰 ฿ {daily_total:,.2f}", "weight": "bold", "size": "md", "color": "#111111", "align": "end"}
                    ]
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": footer_contents
        } if footer_contents else None
    }

def create_error_flex_message(error_msg: str, lang: str) -> dict:
    """Create a Flex Message for Error/Failure."""
    title = "ERROR / FAILURE" if lang == "en" else "เกิดข้อผิดพลาด"
    
    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": title,
                    "weight": "bold",
                    "color": "#EB4E3D",
                    "size": "sm"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": error_msg,
                    "size": "sm",
                    "color": "#111111",
                    "wrap": True
                }
            ]
        }
    }

def create_status_flex_message(sub, usage_count: int, lang: str) -> dict:
    """Create a Status Flex Message for /status command."""
    titles = {
        "en": "SlipSync Status 📊",
        "th": "สถานะ SlipSync 📊",
        "my": "SlipSync အခြေအနေ 📊"
    }
    plan_labels = {"en": "Plan", "th": "แพ็กเกจ", "my": "အမျိုးအစား"}
    expires_labels = {"en": "Expires", "th": "หมดอายุ", "my": "သက်တမ်းကုန်"}
    usage_labels = {"en": "Today's Usage", "th": "ใช้งานวันนี้", "my": "ယနေ့အသုံးပြု"}
    sheet_labels = {"en": "Google Sheet", "th": "Google Sheet", "my": "Google Sheet"}
    
    plan_str = "Pro ✅" if sub.is_paid else "Free Trial 🎁"
    expires_str = sub.trial_expires_at.strftime("%Y-%m-%d") if sub.trial_expires_at else "-"
    usage_str = f"{usage_count} / {sub.rate_limit_daily}"
    sheet_str = "Linked ✅" if sub.gsheet_id else "Not Linked ❌"
    
    footer_contents = []
    if sub.gsheet_id:
        footer_contents.append({
            "type": "button",
            "action": {
                "type": "uri",
                "label": "📊 Open Google Sheet",
                "uri": f"https://docs.google.com/spreadsheets/d/{sub.gsheet_id}"
            },
            "style": "primary",
            "color": "#1DB446",
            "height": "sm"
        })
    
    if not sub.is_paid:
        footer_contents.append({
            "type": "button",
            "action": {
                "type": "message",
                "label": "🚀 Upgrade to Pro",
                "text": "upgrade"
            },
            "style": "secondary",
            "height": "sm",
            "margin": "md"
        })

    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1DB446",
            "contents": [
                {
                    "type": "text",
                    "text": titles.get(lang, titles["en"]),
                    "weight": "bold",
                    "color": "#ffffff",
                    "size": "lg"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": plan_labels.get(lang, plan_labels["en"]), "size": "sm", "color": "#555555", "flex": 0},
                        {"type": "text", "text": plan_str, "size": "sm", "color": "#111111", "align": "end", "weight": "bold"}
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": expires_labels.get(lang, expires_labels["en"]), "size": "sm", "color": "#555555", "flex": 0},
                        {"type": "text", "text": expires_str, "size": "sm", "color": "#111111", "align": "end"}
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": usage_labels.get(lang, usage_labels["en"]), "size": "sm", "color": "#555555", "flex": 0},
                        {"type": "text", "text": usage_str, "size": "sm", "color": "#111111", "align": "end"}
                    ]
                },
                {"type": "separator", "margin": "lg"},
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "contents": [
                        {"type": "text", "text": sheet_labels.get(lang, sheet_labels["en"]), "size": "sm", "color": "#555555", "flex": 0},
                        {"type": "text", "text": sheet_str, "size": "sm", "color": "#111111", "align": "end", "weight": "bold"}
                    ]
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": footer_contents
        } if footer_contents else None
    }

def create_welcome_flex_message(lang: str) -> dict:
    """Create a Flex Message for onboarding with GSheet linking options."""
    titles = {
        "en": "Welcome to SlipSync! 🚀",
        "th": "ยินดีต้อนรับสู่ SlipSync! 🚀",
        "my": "SlipSync မှ ကြိုဆိုပါ! 🚀"
    }
    subtitle = {
        "en": "Sync bank slips to Google Sheets instantly!",
        "th": "ซิงค์สลิปธนาคารลง Google Sheet ทันที!",
        "my": "ဘဏ်စလစ်များကို Google Sheets သို့ ချက်ချင်း sync လုပ်ပါ!"
    }
    choose = {
        "en": "Choose how to connect:",
        "th": "เลือกวิธีเชื่อมต่อ:",
        "my": "ချိတ်ဆက်မည့်နည်းကို ရွေးပါ:"
    }
    option_a = {
        "en": "🔗 I Have a Sheet",
        "th": "🔗 มี Sheet อยู่แล้ว",
        "my": "🔗 Sheet ရှိပြီးသား"
    }
    option_b = {
        "en": "✨ Create New (Coming Soon)",
        "th": "✨ สร้างใหม่อัตโนมัติ (เร็วๆ นี้)",
        "my": "✨ အသစ် ဖန်တီးပါ (မကြာမီ)"
    }

    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1DB446",
            "paddingAll": "lg",
            "contents": [
                {"type": "text", "text": titles.get(lang, titles["en"]), "weight": "bold", "color": "#ffffff", "size": "xl"},
                {"type": "text", "text": subtitle.get(lang, subtitle["en"]), "color": "#ffffffcc", "size": "sm", "margin": "sm"}
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "lg",
            "contents": [
                {"type": "text", "text": choose.get(lang, choose["en"]), "size": "md", "color": "#111111", "weight": "bold"},
                {
                    "type": "button",
                    "action": {"type": "postback", "label": option_a.get(lang, option_a["en"]), "data": "onboard_manual", "displayText": "I have an existing Sheet"},
                    "style": "primary",
                    "color": "#0F9D58",
                    "height": "sm"
                },
                {"type": "text", "text": "— or —", "size": "xs", "color": "#aaaaaa", "align": "center"},
                {
                    "type": "button",
                    "action": {"type": "postback", "label": option_b.get(lang, option_b["en"]), "data": "onboard_auto", "displayText": "Create new sheet automatically"},
                    "style": "secondary",
                    "height": "sm"
                }
            ]
        }
    }

def create_manual_onboard_flex_message(lang: str) -> dict:
    """Create step-by-step Flex Message for manual GSheet linking."""
    step1 = {
        "en": "1️⃣ Open or create your Google Sheet:",
        "th": "1️⃣ เปิดหรือสร้าง Google Sheet:",
        "my": "1️⃣ Google Sheet ဖွင့်ပါ သို့မဟုတ် ဖန်တီးပါ:"
    }
    step2 = {
        "en": "2️⃣ Share your Sheet (Editor access) with:",
        "th": "2️⃣ แชร์ชีต (สิทธิ์ Editor) ให้กับ:",
        "my": "2️⃣ Sheet ကို Editor access ဖြင့် share ပေးပါ:"
    }
    step3 = {
        "en": "3️⃣ Send the Sheet URL here to link it.",
        "th": "3️⃣ ส่ง URL ของ Sheet มาที่นี่เพื่อเชื่อมต่อ",
        "my": "3️⃣ ချိတ်ဆက်ရန် Sheet URL ကို ဒီမှာ ပို့ပါ။"
    }
    step4 = {
        "en": "4️⃣ Send a photo of a bank slip to sync! 📊",
        "th": "4️⃣ ส่งรูปสลิปธนาคารเพื่อบันทึกข้อมูล! 📊",
        "my": "4️⃣ ဘဏ်စလစ်ပုံပို့ပြီး sync လုပ်ပါ! 📊"
    }
    copy_label = {
        "en": "📋 Tap to Copy Email",
        "th": "📋 แตะเพื่อคัดลอกอีเมล",
        "my": "📋 အီးမေးလ် ကူးယူရန် နှိပ်ပါ"
    }

    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#0F9D58",
            "contents": [
                {"type": "text", "text": "Manual Setup 🔗", "weight": "bold", "color": "#ffffff", "size": "md"}
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": step1.get(lang, step1["en"]), "size": "sm", "color": "#111111", "wrap": True},
                {
                    "type": "button",
                    "action": {"type": "uri", "label": "📊 Open Google Sheets", "uri": "https://sheets.new"},
                    "style": "primary",
                    "color": "#0F9D58",
                    "height": "sm"
                },
                {"type": "separator", "margin": "lg"},
                {"type": "text", "text": step2.get(lang, step2["en"]), "size": "sm", "color": "#111111", "wrap": True, "margin": "md"},
                {
                    "type": "text",
                    "text": "slipsync@googlegroups.com",
                    "size": "md",
                    "weight": "bold",
                    "color": "#1DB446",
                    "align": "center",
                    "margin": "md"
                },
                {
                    "type": "button",
                    "action": {"type": "clipboard", "label": copy_label.get(lang, copy_label["en"]), "clipboardText": "slipsync@googlegroups.com"},
                    "style": "secondary",
                    "height": "sm",
                    "margin": "sm"
                },
                {"type": "separator", "margin": "lg"},
                {"type": "text", "text": step3.get(lang, step3["en"]), "size": "sm", "color": "#111111", "wrap": True, "margin": "md"},
                {"type": "text", "text": step4.get(lang, step4["en"]), "size": "sm", "color": "#555555", "wrap": True, "margin": "md", "size": "xs"}
            ]
        }
    }

async def get_user_language(user_id: str) -> str:
    """Detect user language from LINE profile."""
    try:
        async with AsyncApiClient(configuration) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            profile: UserProfileResponse = await line_bot_api.get_profile(user_id)
            if profile.language:
                return profile.language
    except Exception as e:
        logger.error(f"Error fetching user profile for {user_id}: {e}")
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
    body_text = body.decode("utf-8")
    
    try:
        events = parser.parse(body_text, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    for event in events:
        if isinstance(event, MessageEvent):
            if isinstance(event.message, TextMessageContent):
                await process_text(event.source.user_id, event.message.text, event.reply_token)
            elif isinstance(event.message, ImageMessageContent):
                await process_image(event.source.user_id, event.message.id, event.reply_token)
        elif isinstance(event, PostbackEvent):
            await process_postback(event.source.user_id, event.postback.data, event.reply_token)
            
    return "OK"

async def process_text(user_id, text, reply_token):
    lang = await get_user_language(user_id)
    sub = await get_or_create_sub(user_id)
    
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', text)
    if match:
        gsheet_id = match.group(1)
        
        # Check if this sheet already exists
        existing_sheet = await db.sheet.find_first(where={'gsheet_id': gsheet_id})
        if existing_sheet:
            # User is re-linking an existing sheet, just set as active
            await db.subscription.update(where={'id': sub.id}, data={'active_sheet_id': existing_sheet.id})
            reply = f"✅ Sheet already linked! Set as active.\n\nType /invite to share with staff."
        else:
            # Create new Sheet with unique invite code
            invite_code = generate_invite_code()
            # Ensure code is unique
            while await db.sheet.find_unique(where={'invite_code': invite_code}):
                invite_code = generate_invite_code()
            
            new_sheet = await db.sheet.create(
                data={
                    'gsheet_id': gsheet_id,
                    'invite_code': invite_code
                }
            )
            
            # Create membership as manager
            await db.sheetmembership.create(
                data={
                    'subscription_id': sub.id,
                    'sheet_id': new_sheet.id,
                    'role': 'manager'
                }
            )
            
            # Set as active sheet
            await db.subscription.update(where={'id': sub.id}, data={'active_sheet_id': new_sheet.id})
            
            reply = f"✅ **Sheet Linked!**\n\nInvite Code: `{invite_code}`\n\nShare with staff: Type /invite\n\nDon't forget to share Editor access with:\n`{SERVICE_ACCOUNT_EMAIL}`"
    elif "status" in text.lower():
        # Count usage today
        now = datetime.datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        usage_count = await db.usagelog.count(
            where={'subscription_id': sub.id, 'used_at': {'gte': start_of_day}}
        )
        
        # Send Status Flex card
        flex_content = create_status_flex_message(sub, usage_count, lang)
        async with AsyncApiClient(configuration) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            await line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[FlexMessage(alt_text="SlipSync Status", contents=FlexContainer.from_dict(flex_content))]
            ))
        return
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
    elif text.lower().startswith("/invite"):
        # Manager sharing their sheet invite code
        # Get user's active sheet where they are manager
        membership = await db.sheetmembership.find_first(
            where={'subscription_id': sub.id, 'role': 'manager'},
            include={'sheet': True}
        )
        if membership and membership.sheet:
            invite_code = membership.sheet.invite_code
            sheet_name = membership.sheet.name or "Your Sheet"
            invite_flex = {
                "type": "bubble",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#1DB446",
                    "paddingAll": "lg",
                    "contents": [
                        {"type": "text", "text": "📤 Invite Staff", "weight": "bold", "color": "#ffffff", "size": "lg"}
                    ]
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "lg",
                    "contents": [
                        {"type": "text", "text": f"Sheet: {sheet_name}", "size": "sm", "color": "#555555"},
                        {"type": "text", "text": invite_code, "size": "3xl", "weight": "bold", "color": "#1DB446", "align": "center"},
                        {"type": "text", "text": "Share this code with your staff", "size": "xs", "color": "#888888", "align": "center"},
                        {"type": "separator", "margin": "lg"},
                        {"type": "text", "text": "Or share via LINE:", "size": "sm", "color": "#555555", "margin": "lg"},
                        {
                            "type": "button",
                            "action": {
                                "type": "uri",
                                "label": "📤 Share Invite Link",
                                "uri": f"https://line.me/R/msg/text/?Join%20my%20SlipSync%20sheet!%0A%0ACode:%20{invite_code}%0A%0AType%20/join%20{invite_code}%20in%20SlipSync%20bot"
                            },
                            "style": "primary",
                            "color": "#00B900",
                            "height": "sm"
                        }
                    ]
                }
            }
            async with AsyncApiClient(configuration) as api_client:
                line_bot_api = AsyncMessagingApi(api_client)
                await line_bot_api.reply_message(ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[FlexMessage(alt_text=f"Invite Code: {invite_code}", contents=FlexContainer.from_dict(invite_flex))]
                ))
            return
        else:
            reply = "❌ You don't have any sheets yet. Link a Google Sheet first, then you can invite staff!"
    elif text.lower().startswith("/join"):
        # Staff joining a sheet by invite code
        parts = text.split()
        if len(parts) < 2:
            reply = "Usage: /join INVITE_CODE\n\nExample: /join ABC123"
        else:
            invite_code = parts[1].strip().upper()
            sheet = await db.sheet.find_unique(where={'invite_code': invite_code})
            if sheet:
                # Check if already a member
                existing = await db.sheetmembership.find_first(
                    where={'subscription_id': sub.id, 'sheet_id': sheet.id}
                )
                if existing:
                    reply = f"✅ You're already a member of this sheet!"
                else:
                    # Create membership as staff
                    await db.sheetmembership.create(
                        data={
                            'subscription_id': sub.id,
                            'sheet_id': sheet.id,
                            'role': 'staff'
                        }
                    )
                    # Set as active sheet
                    await db.subscription.update(where={'id': sub.id}, data={'active_sheet_id': sheet.id})
                    reply = f"✅ **Joined Sheet Successfully!**\n\nYou're now staff on this sheet. Your processed slips will be forwarded to the manager."
            else:
                reply = "❌ Invalid invite code. Please check with your manager for the correct code."
    else:
        # New user or unknown command -> send welcome Flex card
        flex_content = create_welcome_flex_message(lang)
        async with AsyncApiClient(configuration) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            await line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[FlexMessage(alt_text="Welcome to SlipSync!", contents=FlexContainer.from_dict(flex_content))]
            ))
        return

    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        await line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=reply)]
        ))

# Postback handling is now done in process_postback called from callback

async def process_postback(user_id, data, reply_token):
    if data == 'undo_last':
        lang = await get_user_language(user_id)
        sub = await get_or_create_sub(user_id)
        
        async with AsyncApiClient(configuration) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            # Show loading animation
            try:
                await line_bot_api.show_loading_animation(user_id, 20)
            except Exception: pass
            
            # Send processing text
            processing_text = "กำลังประมวลผลการยกเลิก... ⏳" if lang == "th" else "Processing undo... ⏳"
            await line_bot_api.push_message(PushMessageRequest(to=user_id, messages=[TextMessage(text=processing_text)]))
        
        last_payment = await db.payment.find_first(
            where={'subscription_id': sub.id},
            order={'created_at': 'desc'}
        )
        
        if not last_payment:
            flex_content = create_error_flex_message(get_msg("undo_no_payment", lang), lang)
            messages = [FlexMessage(alt_text="Undo Failed", contents=FlexContainer.from_dict(flex_content))]
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
            
            flex_content = create_undo_flex_message(new_total, lang, sub.gsheet_id)
            messages = [FlexMessage(alt_text="Undo Success", contents=FlexContainer.from_dict(flex_content))]

        async with AsyncApiClient(configuration) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            await line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=messages
            ))
    
    elif data == 'onboard_manual':
        # User chose "I Have a Sheet" - show manual setup steps
        lang = await get_user_language(user_id)
        flex_content = create_manual_onboard_flex_message(lang)
        async with AsyncApiClient(configuration) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            await line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[FlexMessage(alt_text="Manual Setup", contents=FlexContainer.from_dict(flex_content))]
            ))
    
    elif data == 'onboard_auto':
        # User chose "Create New" - LIFF not ready yet, show coming soon message
        lang = await get_user_language(user_id)
        coming_soon = {
            "en": "✨ **Auto-Link Coming Soon!**\n\nThis feature will let you create and link a Sheet with one tap. For now, please use the manual option.\n\nWe're working hard on this! 🚀",
            "th": "✨ **เชื่อมต่ออัตโนมัติ - เร็วๆ นี้!**\n\nฟีเจอร์นี้จะช่วยให้คุณสร้างและเชื่อมต่อ Sheet ได้ในแตะเดียว ตอนนี้กรุณาใช้ตัวเลือกแบบ Manual ก่อนนะคะ\n\nเรากำลังพัฒนาอยู่! 🚀",
            "my": "✨ **Auto-Link မကြာမီလာမည်!**\n\nဤ feature ဖြင့် Sheet ကို တစ်ချက်နှိပ်ရုံဖြင့် ဖန်တီးပြီး ချိတ်ဆက်နိုင်မည်။ ယခုအချိန်တွင် manual option ကို အသုံးပြုပါ။\n\nကျွန်ုပ်တို့ ကြိုးစားနေပါသည်! 🚀"
        }
        async with AsyncApiClient(configuration) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            await line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=coming_soon.get(lang, coming_soon["en"]))]
            ))

async def process_image(user_id, msg_id, reply_token):
    lang = await get_user_language(user_id)
    sub = await get_or_create_sub(user_id)
    
    # Check limit
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        allowed, reason = await check_usage_and_rate_limit(sub, lang)
        if not allowed:
            await line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=reason)]
            ))
            return

    # Show loading animation
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        try:
            await line_bot_api.show_loading_animation(user_id, 30) # 30 seconds max
        except Exception as e:
            logger.warning(f"Failed to show loading animation: {e}")
        
        # Send processing text in Thai/English
        proc_text = "กำลังประมวลผลสลิปของคุณ... ⏳" if lang == "th" else "Processing your slip... ⏳"
        await line_bot_api.push_message(PushMessageRequest(to=user_id, messages=[TextMessage(text=proc_text)]))

    # Image download & save
    async with AsyncApiClient(configuration) as api_client:
        api_blob = AsyncMessagingApiBlob(api_client)
        try:
            content = await api_blob.get_message_content(msg_id)
            image_bytes = content
        except Exception as e:
            logger.error(f"Failed to download image from LINE: {e}")
            line_bot_api = AsyncMessagingApi(api_client)
            error_card = create_error_flex_message("Failed to download image. Please try again later.", lang)
            await line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[FlexMessage(alt_text="Error", contents=FlexContainer.from_dict(error_card))]
            ))
            return

    filename = f"line_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{user_id}.jpg"
    image_path = os.path.join(IMAGE_DIR, filename)
    with open(image_path, "wb") as f:
        f.write(image_bytes)

    # OCR & GSheet
    data = await extract_data_from_image(image_bytes)
    if not data:
        async with AsyncApiClient(configuration) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            error_card = create_error_flex_message(get_msg("ocr_failed", lang), lang)
            await line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[FlexMessage(alt_text="OCR Failed", contents=FlexContainer.from_dict(error_card))]
            ))
        return

    # Check for Auto-Upgrade (PromptPay OCR)
    if not sub.is_paid and PROMPTPAY_RECEIVER_NAME != "YOUR NAME HERE":
        extracted_receiver = str(data.get('receiver_name', '')).upper()
        if PROMPTPAY_RECEIVER_NAME.upper() in extracted_receiver:
            await db.subscription.update(
                where={'id': sub.id},
                data={'is_paid': True, 'rate_limit_daily': 1000}
            )
            sub.is_paid = True
            async with AsyncApiClient(configuration) as api_client:
                line_bot_api = AsyncMessagingApi(api_client)
                await line_bot_api.push_message(PushMessageRequest(to=user_id, messages=[TextMessage(text=get_msg("pro_upgrade_success", lang))]))
            logger.info(f"Auto-upgraded LINE subscription {sub.id} via OCR.")

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
    slips_count = len(payments)

    # Prepare Flex Message
    flex_content = create_payment_flex_message(data, daily_sum, slips_count, sub.gsheet_id)
    quick_reply = QuickReply(items=[
        QuickReplyItem(action=PostbackAction(label="Undo ↩️", data="undo_last", display_text="Undo Last Action"))
    ])
    
    flex_message = FlexMessage(
        alt_text=f"Payment Summary: {data.get('amount')} {data.get('currency')}",
        contents=FlexContainer.from_dict(flex_content),
        quick_reply=quick_reply
    )

    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        
        # Reply with Flex Message
        await line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[flex_message]
        ))
        
        # Get gsheet_id from active sheet
        gsheet_id = None
        if sub.active_sheet_id:
            active_sheet = await db.sheet.find_unique(where={'id': sub.active_sheet_id})
            if active_sheet:
                gsheet_id = active_sheet.gsheet_id
        
        # Fallback to legacy gsheet_id if no active sheet
        if not gsheet_id:
            gsheet_id = sub.gsheet_id
        
        # Then we push GSheet update notification if successful
        if gsheet_id:
            success = update_gsheet(data, image_path, gsheet_id)
            if not success:
                error_card = create_error_flex_message(get_msg("link_instr", lang), lang)
                await line_bot_api.push_message(PushMessageRequest(to=user_id, messages=[FlexMessage(alt_text="GSheet Error", contents=FlexContainer.from_dict(error_card))]))
        else:
            # No sheet linked, show error
            error_card = create_error_flex_message(get_msg("link_instr", lang), lang)
            await line_bot_api.push_message(PushMessageRequest(to=user_id, messages=[FlexMessage(alt_text="No Sheet Linked", contents=FlexContainer.from_dict(error_card))]))
        
        # Forward slip to sheet managers if user is staff
        if sub.active_sheet_id:
            try:
                # Check if user is staff on this sheet
                my_membership = await db.sheetmembership.find_first(
                    where={'subscription_id': sub.id, 'sheet_id': sub.active_sheet_id}
                )
                if my_membership and my_membership.role == 'staff':
                    # Find all managers of this sheet
                    manager_memberships = await db.sheetmembership.find_many(
                        where={'sheet_id': sub.active_sheet_id, 'role': 'manager'},
                        include={'subscription': {'include': {'users': True}}}
                    )
                    for mm in manager_memberships:
                        if mm.subscription and mm.subscription.users:
                            for user in mm.subscription.users:
                                if user.platform == 'line':
                                    fwd_msg = f"📨 **New Slip from Staff**\n\n💰 {data.get('amount'):,.2f} {data.get('currency', 'THB')}\n📅 {data.get('date')} {data.get('time')}\n👤 Sender: {data.get('sender_name', '-')}"
                                    await line_bot_api.push_message(PushMessageRequest(to=user.platform_id, messages=[TextMessage(text=fwd_msg)]))
            except Exception as fwd_err:
                logger.error(f"Failed to forward slip to manager: {fwd_err}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
