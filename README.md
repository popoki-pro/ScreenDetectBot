# ScreenDetectBot

A desktop GUI tool that monitors a selected screen region and sends a Telegram message when a change is detected. Supports two detection modes: pixel-level color change detection and OCR-based text matching.

---

## Features

- **Color Detection** — detects any visual change in the monitored region using per-pixel comparison
- **Text Detection** — runs OCR on the region and alerts when specific text appears (Contains / Exact / Regex)
- Monitor multiple Telegram chat IDs simultaneously
- Configurable check interval (1–3600 seconds)
- Duplicate suppression — never resends the same detected text twice in a row across restarts
- All settings encrypted and persisted automatically (`.env` + `.key`)
- Live log panel with a Clear button

---

## Requirements

- Python 3.8+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (required for Text Detection mode only)
- A Telegram bot token — create one via [@BotFather](https://t.me/BotFather)
- The Chat ID(s) you want to notify

### Install Tesseract

**Ubuntu / Debian**
```bash
sudo apt install tesseract-ocr
```

**macOS**
```bash
brew install tesseract
```

**Windows**
Download the installer from the [Tesseract releases page](https://github.com/UB-Mannheim/tesseract/wiki).

---

## Installation

```bash
pip install -r requirements.txt
```

---

## Usage

```bash
python main.py
```

1. Select a **detection mode** from the dropdown: **Color Detection** or **Text Detection**.
2. Click **Settings** and configure:
   - **Telegram** — bot token, chat IDs, alert message, and check interval
   - **Text Detection** *(text mode only)* — match mode and target text list
   - **Screen Area** — click **Select Area** and drag a rectangle over the region to monitor
3. Click **Start** to begin monitoring.
4. Click **Stop** to stop at any time.

All settings are saved automatically when the window is closed and restored on the next launch.

---

## Detection Modes

### Color Detection

Captures a screenshot of the selected region at each interval and compares it to the previous frame using per-pixel max-channel difference. A Telegram message is sent when any pixel changes by more than 10 intensity units.

- If the **Message** field is empty, sends `"A screen change has been detected."`

### Text Detection

Runs Tesseract OCR on the selected region at each interval and checks the extracted text against your configured target list. A message is sent on the first match.

- **Contains** — case-insensitive substring match (e.g. target `"error"` matches `"An Error occurred"`)
- **Exact** — trimmed OCR output must equal the target exactly
- **Regex** — target is treated as a Python regular expression

If the **Message** field is empty, the full detected text is sent instead. A message is never resent if the detected text has not changed since the last send — this persists across stop/start cycles.

---

## Settings Storage

Settings are encrypted with [Fernet](https://cryptography.io/en/latest/fernet/) symmetric encryption and stored in two local files:

| File | Purpose |
|------|---------|
| `.key` | Auto-generated encryption key (never commit this) |
| `.env` | Encrypted settings: token, chat IDs, message, interval, targets, last detected text, selected area, mode |

Both files are excluded from version control via `.gitignore`.

---

## Getting Your Chat ID

1. Send any message to your bot.
2. Open the following URL in a browser (replace with your token):

```
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

3. Look for `"chat": {"id": <number>}` in the response — that number is your Chat ID.

---

## Project Structure

```
main.py          # Unified app entry point
storage.py       # Encryption / .env read-write helpers
requirements.txt
.env             # Encrypted settings (auto-generated, git-ignored)
.key             # Fernet encryption key (auto-generated, git-ignored)
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| PyQt5 | Desktop GUI |
| mss | Fast cross-platform screen capture |
| numpy | Pixel-level frame comparison |
| Pillow | Image conversion for OCR |
| pytesseract | Python wrapper for Tesseract OCR |
| requests | Telegram Bot API calls |
| cryptography | Fernet encryption for stored settings |
