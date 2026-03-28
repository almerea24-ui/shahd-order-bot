# Shahd Beauty - Telegram Order Bot

Telegram bot that automatically enters WhatsApp orders into Odoo ERP.

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API Token | Yes |
| `ODOO_URL` | Odoo instance URL | Yes |
| `ODOO_DB` | Odoo database name | Yes |
| `ODOO_USER` | Odoo username | Yes |
| `ODOO_PASSWORD` | Odoo password | Yes |
| `OPENAI_API_KEY` | OpenAI API key for order parsing | Yes |
| `OPENAI_BASE_URL` | OpenAI API base URL (if using proxy) | No |

## Commands

- `/start` - Start the bot
- `/shahd` - Set brand to Shahd Beauty
- `/marlin` - Set brand to Marlin
- `/help` - Show help

## Deploy on Railway

1. Push this repo to GitHub
2. Connect Railway to the GitHub repo
3. Set environment variables in Railway dashboard
4. Deploy!
