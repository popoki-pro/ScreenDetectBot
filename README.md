# ScreenDetectBot

A desktop GUI tool that monitors a selected screen region for visual changes and sends a Telegram message when a change is detected.

## Features

- Draw a selection box over any part of your screen to define the monitored region
- Pixel-level change detection using NumPy
- Sends a configurable Telegram message to one or more chat IDs on detection
- Adjustable check interval (1–3600 seconds)
- Live log panel showing detection events and send results

## Requirements

- Python 3.8+
- A Telegram bot token (create one via [@BotFather](https://t.me/BotFather))
- The Chat ID(s) you want to notify

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

1. Select a **detection mode** from the dropdown — **Color Detection** or **Text Detection**.
2. Click **Settings** to configure:
   - **Telegram** — bot token, chat IDs, message, and check interval
   - **Text Detection** (text mode only) — match mode (Contains / Exact / Regex) and target texts
   - **Screen Area** — drag a rectangle over the region to monitor
3. Click **Start** to begin monitoring.
4. Click **Stop** to stop.

Settings (token, chat IDs, message, targets, last detected text) are encrypted and saved automatically on exit.

## Detection Modes

### Color Detection
Captures frames at the configured interval and compares each one to the previous using per-pixel difference. If any pixel changes by more than 10 intensity units, a Telegram message is sent. If the message field is empty, **"A screen change has been detected."** is sent.

### Text Detection
Runs OCR (via Tesseract) on the selected region and checks the extracted text against your configured target list. Supports three match modes:
- **Contains** — case-insensitive substring match
- **Exact** — full string equality
- **Regex** — Python regex pattern

If the message field is empty, the detected text itself is sent. A message is never resent if the detected text has not changed since the last send.

## Getting Your Chat ID

Send a message to your bot, then open:

```
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

Look for `"chat": {"id": ...}` in the response.
