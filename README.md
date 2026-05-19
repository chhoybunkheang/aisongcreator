# ai-song-bot

Project scaffold for the AI Song Bot.

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
