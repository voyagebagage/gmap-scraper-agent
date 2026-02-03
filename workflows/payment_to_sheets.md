---
description: how to use the telegram payment bot
---

# Telegram Payment Bot Workflow

This workflow describes how to set up and use the Telegram bot to transcribe Thai bank payment slips into a Google Sheet.

## Setup

1.  **Environment Variables**:
    - Open `.env` and add:
      ```env
      SLIPSYNC_BOT_TOKEN=your_telegram_bot_token
      BOT_GSHEET_ID=your_google_sheet_id
      ```
2.  **Dependencies**:
    - Install required packages:
      ```bash
      pip install python-telegram-bot google-generativeai gspread oauth2client python-dotenv
      ```
3.  **Start the Bot**:
    - Run the script:
      ```bash
      python3 tools/payment_bot.py
      ```

## Usage

1.  **Take a Photo**: Take a clear photo or screenshot of a Thai bank payment slip (e.g., KBank, SCB, PromptPay).
2.  **Send to Bot**: Send the image to the Telegram bot.
3.  **Automatic Transcription**:
    - The bot will process the image using Gemini Vision.
    - It will extract the **Customer Name**, **Amount**, **Time**, and **Reference Number**.
    - It will send a confirmation message back to you.
4.  **View in Google Sheets**:
    - Open the Google Sheet specified by `BOT_GSHEET_ID`.
    - The data will be appended and sorted by the payment time.
