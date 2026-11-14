import asyncio
import logging
import os
import io
from datetime import datetime, date, timedelta
import pytz
from aiohttp import web
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import Database
from media_utils import (
    generate_mood_chart, create_monthly_collage,
    generate_365_pdf_book, get_weekly_ai_advice
)

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CREATOR_USERNAME = os.getenv("CREATOR_USERNAME", "@Abdullayev24")
DATABASE_URL = os.getenv("DATABASE_URL")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")
TZ = pytz.timezone(os.getenv("TIMEZONE", "Asia/Tashkent"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
db = Database(DATABASE_URL)

class AddTaskState(StatesGroup):
    waiting_title = State()
    waiting_target = State()

class DailyLogState(StatesGroup):
    waiting_task = State()
    waiting_amount = State()
    waiting_note = State()
    waiting_photo = State()
    waiting_mood = State()

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Vazifa qo'shish"), KeyboardButton(text="✅ Bugungi natija")],
            [KeyboardButton(text="📊 Hisobot"), KeyboardButton(text="🏆 Daraja va Medallar")],
            [KeyboardButton(text="🤖 Haftalik AI Xulosa"), KeyboardButton(text="🖼 Oylik Kollaj")],
            [KeyboardButton(text="📖 Mening 365 kunim (PDF)"), KeyboardButton(text="📋 Vazifalarim")]
        ],
        resize_keyboard=True
    )

def mood_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="🤩 Zo'r", callback_data="mood_🤩"),
            InlineKeyboardButton(text="😊 Yaxshi", callback_data="mood_😊"),
            InlineKeyboardButton(text="😐 O'rtacha", callback_data="mood_😐"),
            InlineKeyboardButton(text="😔 Charchoq", callback_data="mood_😔"),
            InlineKeyboardButton(text="😫 Qiyin", callback_data="mood_😫")
        ]]
    )

def skip_photo_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⏭ Rasmsiz davom etish", callback_data="skip_photo")]]
    )

def report_period_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="📅 Kunlik", callback_data="rep_daily"),
            InlineKeyboardButton(text="📆 Haftalik", callback_data="rep_weekly"),
            InlineKeyboardButton(text="🗓 Oylik + Grafik", callback_data="rep_monthly")
        ]]
    )

@router.message(CommandStart())
async def cmd_start(message: Message):
    await db.register_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.full_name
    )
    text = (
        f"Assalomu alaykum, <b>{message.from_user.full_name}</b>! 👋

"
        f"Shaxsiy intizom va kunlik natijalar botiga xush kelibsiz.
"
        f"Har kuni soat <b>07:00</b> da sizga motivatsiya va kunlik vazifalaringiz eslatiladi.

"
        f"👨‍💻 Yaratuvchi: {CREATOR_USERNAME}"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu())

@router.message(F.text == "➕ Vazifa qo'shish")
async def start_add_task(message: Message, state: FSMContext):
    await state.set_state(AddTaskState.waiting_title)
    await message.answer("Yangi kunlik vazifa nomini yozing (masalan: <i>Kitob o'qish</i> yoki <i>Turnik</i>):", parse_mode="HTML")

@router.message(AddTaskState.waiting_title)
async def process_task_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(AddTaskState.waiting_target)
    await message.answer("Kunlik maqsad miqdorini yozing (masalan: <i>20 bet</i> yoki <i>30 daqiqa</i>):", parse_mode="HTML")

@router.message(AddTaskState.waiting_target)
async def process_task_target(message: Message, state: FSMContext):
    data = await state.get_data()
    await db.add_task(message.from_user.id, data['title'], message.text)
    await state.clear()
    await message.answer(f"✅ Vazifa saqlandi: <b>{data['title']}</b> ({message.text})", parse_mode="HTML", reply_markup=main_menu())

@router.message(F.text == "📋 Vazifalarim")
async def list_tasks(message: Message):
    tasks = await db.get_active_tasks(message.from_user.id)
    if not tasks:
        await message.answer("Hozircha faol vazifalar yo'q. ➕ Vazifa qo'shish tugmasini bosing.")
        return

    kb_rows = []
    lines = ["📋 <b>Sizning kunlik vazifalaringiz:</b>
"]
    for idx, t in enumerate(tasks, 1):
        lines.append(f"{idx}. <b>{t['title']}</b> — {t['target_amount']}")
        kb_rows.append([InlineKeyboardButton(text=f"❌ O'chirish: {t['title']}", callback_data=f"deltask_{t['id']}")])

    await message.answer("
".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

@router.callback_query(F.data.startswith("deltask_"))
async def delete_task_cb(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    await db.delete_task(task_id, callback.from_user.id)
    await callback.answer("Vazifa o'chirildi!")
    await callback.message.edit_text("🗑 Vazifa ro'yxatdan o'chirildi.")

@router.message(F.text == "✅ Bugungi natija")
async def start_daily_log(message: Message, state: FSMContext):
    tasks = await db.get_active_tasks(message.from_user.id)
    await state.set_state(DailyLogState.waiting_task)

    if tasks:
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=t['title'])] for t in tasks] + [[KeyboardButton(text="✍️ Boshqa vazifa")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer("Bugun qaysi vazifani bajardingiz? Ro'yxatdan tanlang yoki nomini yozing:", reply_markup=kb)
    else:
        await message.answer("Bugun nima ish qildingiz? Vazifa nomini yozing:")

@router.message(DailyLogState.waiting_task)
async def log_task_name(message: Message, state: FSMContext):
    if message.text == "✍️ Boshqa vazifa":
        await message.answer("Vazifa nomini yozing:")
        return
    await state.update_data(task_title=message.text)
    await state.set_state(DailyLogState.waiting_amount)
    await message.answer("Qancha bajardingiz? (masalan: <i>25 bet o'qidim</i>, <i>3x15 turnik</i>):", parse_mode="HTML", reply_markup=main_menu())

@router.message(DailyLogState.waiting_amount)
async def log_amount(message: Message, state: FSMContext):
    await state.update_data(done_amount=message.text)
    await state.set_state(DailyLogState.waiting_note)
    await message.answer("Bugun haqida bir-ikki gap yozing (kunlik xulosa yoki taassurot):")

@router.message(DailyLogState.waiting_note)
async def log_note(message: Message, state: FSMContext):
    await state.update_data(note=message.text)
    await state.set_state(DailyLogState.waiting_photo)
    await message.answer("Bugungi natijadan esdalik rasm yuboring (oy oxirida kollaj tayyorlanadi):", reply_markup=skip_photo_kb())

@router.callback_query(DailyLogState.waiting_photo, F.data == "skip_photo")
async def skip_photo_cb(callback: CallbackQuery, state: FSMContext):
    await state.update_data(photo_id=None)
    await state.set_state(DailyLogState.waiting_mood)
    await callback.message.edit_text("Bugungi kayfiyatingizni tanlang:", reply_markup=mood_keyboard())

@router.message(DailyLogState.waiting_photo, F.photo)
async def log_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    await state.set_state(DailyLogState.waiting_mood)
    await message.answer("Ajoyib rasm! Endi bugungi kayfiyatingizni tanlang:", reply_markup=mood_keyboard())

@router.callback_query(DailyLogState.waiting_mood, F.data.startswith("mood_"))
async def finish_daily_log(callback: CallbackQuery, state: FSMContext):
    mood = callback.data.split("_")[1]
    data = await state.get_data()
    today = datetime.now(TZ).date()

    streak, new_medals = await db.add_daily_log(
        user_id=callback.from_user.id,
        log_date=today,
        task_title=data['task_title'],
        done_amount=data['done_amount'],
        note=data['note'],
        mood=mood,
        photo_file_id=data.get('photo_id')
    )
    await state.clear()

    msg = (
        f"🎉 <b>Bugungi natija saqlandi!</b>

"
        f"📌 Vazifa: {data['task_title']}
"
        f"📈 Natija: {data['done_amount']}
"
        f"📝 Yozuv: <i>{data['note']}</i>
"
        f"🎭 Kayfiyat: {mood}
"
        f"🔥 Joriy seriya: <b>{streak} kun</b>"
    )
    if new_medals:
        msg += "

🎊 <b>YANGI MEDAL QO'LGA KIRITILDI!</b>
" + "
".join(new_medals)

    await callback.message.edit_text(msg, parse_mode="HTML")

@router.message(F.text == "📊 Hisobot")
async def ask_report(message: Message):
    await message.answer("Qaysi davr uchun hisobot kerak?", reply_markup=report_period_kb())

@router.callback_query(F.data.startswith("rep_"))
async def send_report(callback: CallbackQuery):
    period = callback.data.split("_")[1]
    today = datetime.now(TZ).date()
    user_id = callback.from_user.id

    if period == "daily":
        start_date = today
        title = f"📅 Kunlik Hisobot ({today})"
    elif period == "weekly":
        start_date = today - timedelta(days=6)
        title = f"📆 Haftalik Hisobot ({start_date} — {today})"
    else:
        start_date = today.replace(day=1)
        title = f"🗓 Oylik Hisobot ({today.strftime('%Y-%m')})"

    logs = await db.get_logs_by_range(user_id, start_date, today)
    if not logs:
        await callback.message.edit_text("Bu davr uchun hali natijalar kiritilmagan.")
        return

    lines = [f"<b>{title}</b>
"]
    for l in logs[-20:]:
        lines.append(f"• <b>{l['log_date'].strftime('%d.%m')}</b> | {l['task_title']}: {l['done_amount']} {l['mood']}")

    await callback.message.edit_text("
".join(lines), parse_mode="HTML")

    if period == "monthly":
        chart_buf = generate_mood_chart(logs, today.strftime("%B %Y"))
        if chart_buf:
            await callback.message.answer_photo(
                BufferedInputFile(chart_buf.read(), filename="mood_chart.png"),
                caption="📈 Oylik kayfiyat dinamikasi grafigi"
            )

@router.message(F.text == "🏆 Daraja va Medallar")
async def show_medals(message: Message):
    stats = await db.get_user_stats(message.from_user.id)
    if not stats:
        return
    streak = stats['streak']
    max_streak = stats['max_streak']
    medals = stats['medals'] or []

    if streak < 7:
        rank = "🌱 Boshlang'ich"
    elif streak < 30:
        rank = "⚡ Intizomli"
    elif streak < 100:
        rank = "🔥 Temir Iroda"
    elif streak < 365:
        rank = "👑 Master"
    else:
        rank = "🏆 365 Kunlik Afsona"

    medals_text = "
".join(medals) if medals else "Hali medallar yo'q (7, 30, 100, 365 kunlik seriyalarda ochiladi)"
    text = (
        f"🏆 <b>Sizning Daraja va Yutuqlaringiz</b>

"
        f"🎖 Daraja: <b>{rank}</b>
"
        f"🔥 Joriy uzluksiz seriya: <b>{streak} kun</b>
"
        f"⭐ Eng uzun seriya: <b>{max_streak} kun</b>

"
        f"<b>Qo'lga kiritilgan medallar:</b>
{medals_text}"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(F.text == "🤖 Haftalik AI Xulosa")
async def weekly_ai_summary(message: Message):
    wait_msg = await message.answer("🤖 AI haftalik natijalaringizni tahlil qilmoqda...")
    today = datetime.now(TZ).date()
    logs = await db.get_logs_by_range(message.from_user.id, today - timedelta(days=7), today)
    advice = await get_weekly_ai_advice(logs)
    await wait_msg.edit_text(f"🤖 <b>Haftalik AI Xulosa va Maslahat:</b>

{advice}", parse_mode="HTML")

@router.message(F.text == "🖼 Oylik Kollaj")
async def monthly_collage(message: Message):
    wait_msg = await message.answer("🖼 Rasmlardan oylik kollaj tayyorlanmoqda...")
    today = datetime.now(TZ).date()
    logs = await db.get_logs_by_range(message.from_user.id, today.replace(day=1), today)
    photo_ids = [l['photo_file_id'] for l in logs if l['photo_file_id']]

    if not photo_ids:
        await wait_msg.edit_text("Bu oyda hali rasmli natijalar yuklanmagan.")
        return

    img_bytes = []
    for pid in photo_ids[-16:]:
        file = await bot.get_file(pid)
        b = io.BytesIO()
        await bot.download_file(file.file_path, b)
        img_bytes.append(b.getvalue())

    collage_buf = create_monthly_collage(img_bytes)
    if collage_buf:
        await wait_msg.delete()
        await message.answer_photo(
            BufferedInputFile(collage_buf.read(), filename="monthly_collage.jpg"),
            caption=f"🖼 <b>{today.strftime('%B %Y')} — Oylik Xotiralar Kollaji</b>",
            parse_mode="HTML"
        )
    else:
        await wait_msg.edit_text("Kollaj yaratishda xatolik yuz berdi.")

@router.message(F.text == "📖 Mening 365 kunim (PDF)")
async def export_365_pdf(message: Message):
    wait_msg = await message.answer("📖 'Mening 365 kunim' PDF kitobi tuzilmoqda...")
    today = datetime.now(TZ).date()
    start_year = date(today.year, 1, 1)
    logs = await db.get_logs_by_range(message.from_user.id, start_year, today)
    stats = await db.get_user_stats(message.from_user.id)

    pdf_buf = generate_365_pdf_book(message.from_user.full_name, today.year, logs, dict(stats))
    await wait_msg.delete()
    await message.answer_document(
        BufferedInputFile(pdf_buf.read(), filename=f"Mening_365_Kunim_{today.year}.pdf"),
        caption="📖 Sizning shaxsiy yillik kitobingiz tayyor!"
    )

async def morning_motivation_job():
    users = await db.get_all_users()
    motivations = [
        "Yangi kun — yangi imkoniyat! Bugungi kichik qadamlar ertangi katta g'alabalarni yaratadi.",
        "Intizom — bu kayfiyatga qaramasdan o'z so'zingda turishdir. Boshladik!",
        "Kecha qilgan mehnating bugungi kuchingdir. Rejalarni bajarish vaqti keldi!"
    ]
    quote = motivations[datetime.now(TZ).day % len(motivations)]

    for u in users:
        tasks = await db.get_active_tasks(u['user_id'])
        task_list = "
".join([f"▫️ {t['title']} ({t['target_amount']})" for t in tasks]) if tasks else "▫️ Hali vazifa qo'shilmagan"
        text = (
            f"☀️ <b>Xayrli tong, {u['full_name']}!</b>

"
            f"💡 <i>"{quote}"</i>

"
            f"📋 <b>Bugungi vazifalaringiz:</b>
{task_list}

"
            f"🔥 Joriy seriya: {u['streak']} kun"
        )
        try:
            await bot.send_message(u['user_id'], text, parse_mode="HTML")
        except Exception:
            pass

async def health_check(request):
    return web.Response(text="Bot is alive and running!")

async def keep_alive_ping():
    if not RENDER_URL:
        return
    import aiohttp
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{RENDER_URL}/health") as resp:
                logging.info(f"Anti-sleep ping status: {resp.status}")
        except Exception as e:
            logging.warning(f"Anti-sleep ping failed: {e}")

async def main():
    await db.connect()
    dp.include_router(router)

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(morning_motivation_job, "cron", hour=7, minute=0)
    scheduler.add_job(keep_alive_ping, "interval", minutes=10)
    scheduler.start()

    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logging.info(f"Web server running on port {port}. Starting polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
