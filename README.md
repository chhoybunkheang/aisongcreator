# ai-song-bot

Project scaffold for the AI Song Bot.

## Railway deployment

The bot can run on Railway in polling mode with `python run.py`, but production deployment should use persistent storage settings instead of the local defaults.

### Required variables

Set these variables in Railway:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
OPENAI_API_KEY=your_openai_key
ADMIN_ID=123456789
BOT_USERNAME=your_bot_username
DATABASE_URL=postgresql://...
MEDIA_ROOT=/data/media
DATA_ROOT=/data
```

Notes:

- `DATABASE_URL` should point to a Railway Postgres service.
- `MEDIA_ROOT` and `DATA_ROOT` should point to a mounted Railway volume if you want generated songs, covers, videos, and SQLite fallback data to persist across redeploys.
- If `DATABASE_URL` is omitted, the app falls back to SQLite at `DATA_ROOT/users.db`.

### Start command

Use this Railway start command:

```bash
python run.py
```

### Persistent storage

This bot writes generated media files to disk. For Railway production deployments, attach a volume and mount it to `/data`, then set:

```env
MEDIA_ROOT=/data/media
DATA_ROOT=/data
```

Without a volume, generated media and local SQLite data may be lost on redeploy or container restart.

## Payment configuration

Set these environment variables to enable the QR/manual credit purchase flow:

```env
PAYMENT_QR_IMAGE=media/payment-qr.png
PAYMENT_ACCOUNT_NUMBER=012345678
PAYMENT_ACCOUNT_NAME=YOUR NAME
PAYMENT_SCREENSHOT_AI_ENABLED=true
```

Users pay by scanning your QR or following your manual account instructions, then upload a screenshot for review.

When `PAYMENT_SCREENSHOT_AI_ENABLED=true`, uploaded payment screenshots are analyzed by AI and sent to admin with a recommendation. Admin approval is still required.

## Khmer singing fallback models

If Khmer vocals drift into another language, set alternate music model IDs for Khmer requests:

```env
MUSIC_MODEL=Qubico/ace-step
KHMER_MODEL_CANDIDATES=Qubico/ace-step
```

`KHMER_MODEL_CANDIDATES` accepts a comma-separated list. Put your best Khmer-capable music models first. The bot will try each model in order and reject outputs that do not verify as Khmer.
