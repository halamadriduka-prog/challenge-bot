# Telegram Shaxsiy Rivojlanish va Intizom Boti (@Abdullayev24)

## Loyiha tuzilmasi
- `bot.py` — Asosiy Telegram bot (Aiogram 3), 07:00 eslatmalar (APScheduler) va Render anti-sleep web-serveri.
- `database.py` — Neon PostgreSQL ma'lumotlar bazasi bilan asinxron ishlash (`asyncpg`).
- `media_utils.py` — Oylik kayfiyat grafigi (`matplotlib`), oylik rasmlar kollaji (`Pillow`), "Mening 365 kunim" PDF kitobi (`reportlab`) va haftalik AI maslahatchi (`Gemini`).
- `requirements.txt` — Kerakli kutubxonalar ro'yxati.
- `.env` — Token va maxfiy kalitlar.

## 1-qadam: Neon.tech (PostgreSQL baza) sozlash
1. [neon.tech](https://neon.tech) saytida bepul ro'yxatdan o'ting va **New Project** yarating.
2. Dashboard'dagi **Connection String** (PostgreSQL URL) nusxasini oling (masalan: `postgresql://user:pass@ep-xyz.aws.neon.tech/neondb?sslmode=require`).

## 2-qadam: GitHub'ga yuklash
1. Ushbu papkadagi barcha fayllarni GitHub'da yangi **Private** (yopiq) repository ochib yuklang (`.env` faylini GitHub'ga yuklamang, o'zgaruvchilarni Render'ga kiritasiz).

## 3-qadam: Render.com da ishga tushirish
1. [render.com](https://render.com) ga kiring -> **New +** -> **Web Service** ni tanlang (Background Worker emas, chunki tekin tarifda Web Service + Anti-Sleep ishlaydi).
2. GitHub repository'ni ulang.
3. Sozlamalar:
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
4. **Environment Variables** bo'limiga quyidagilarni qo'shing:
   - `BOT_TOKEN` = `9095816567:AAHm8FQPUSDOVsvwRuTJnhkcxpry-BTeDOM`
   - `CREATOR_USERNAME` = `@Abdullayev24`
   - `DATABASE_URL` = *(Neon.tech dan olingan URL)*
   - `GEMINI_API_KEY` = *(Google AI Studio dan olingan API kalit)*
   - `RENDER_EXTERNAL_URL` = *(Render bergan sayt manzili, masalan `https://my-bot.onrender.com`)*
   - `TIMEZONE` = `Asia/Tashkent`

## 4-qadam: Bot uxlab qolmasligini 100% kafolatlash
Kod ichida har 10 daqiqada o'zini ping qiluvchi tizim yozilgan. Qo'shimcha kafolat uchun [uptimerobot.com](https://uptimerobot.com) saytida bepul monitor ochib, Render URL manzilingizni (`https://sizning-bot.onrender.com/health`) har 5 daqiqada tekshiruvga qo'yib qo'ying. Shunda bot 24/7 umuman uxlamaydi.
