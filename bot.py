import asyncio
import logging
import os
import io
from datetime import datetime, date, timedelta
import pytz
from aiohttp import web
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
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

WEEKDAYS_SHORT = {
    0: "Du", 1: "Se", 2: "Ch", 3: "Pa", 4: "Ju", 5: "Sh", 6: "Ya"
}

def format_days_label(days_str: str) -> str:
    if not days_str or days_str == "all":
        return "Har kuni"
    idxs = sorted([int(x) for x in days_str.split(",") if x.isdigit()])
    if len(idxs) == 7:
        return "Har kuni"
    return ", ".join([WEEKDAYS_SHORT[i] for i in idxs])

class AddTaskState(StatesGroup):
    waiting_title = State()
    waiting_target = State()
    waiting_days = State()

class DailyJournalState(StatesGroup):
    waiting_note = State()
    waiting_photo = State()
    waiting_mood = State()

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Bugungi vazifalar"), KeyboardButton(text="✏️ Oldingi kunlarni tahrirlash")],
            [KeyboardButton(text="➕ Vazifa qo'shish"), KeyboardButton(text="📝 Kunlik yozuv (Eslatma)")],
            [KeyboardButton(text="📊 Hisobot"), KeyboardButton(text="🏆 Daraja va Medallar")],
            [KeyboardButton(text="🤖 Haftalik AI Xulosa"), KeyboardButton(text="🖼 Oylik Kollaj")],
            [KeyboardButton(text="📖 Mening 365 kunim (PDF)"), KeyboardButton(text="📋 Barcha vazifalar")],
            [KeyboardButton(text="ℹ️ Bot haqida")]
        ],
        resize_keyboard=True
    )

def days_selector_keyboard(selected: set):
    row1 = []
    for d in range(4):
        mark = "✅" if d in selected else "⬜"
        row1.append(InlineKeyboardButton(text=f"{mark} {WEEKDAYS_SHORT[d]}", callback_data=f"tday_{d}"))
    row2 = []
    for d in range(4, 7):
        mark = "✅" if d in selected else "⬜"
        row2.append(InlineKeyboardButton(text=f"{mark} {WEEKDAYS_SHORT[d]}", callback_data=f"tday_{d}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Har kuni (7 kun)", callback_data="tday_all")],
        row1,
        row2,
        [InlineKeyboardButton(text="💾 Saqlash", callback_data="tday_save")]
    ])

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

async def build_checklist_ui_for_date(user_id: int, target_date: date):
    today = datetime.now(TZ).date()
    weekday_idx = target_date.weekday()
    tasks = await db.get_tasks_for_date(user_id, target_date)
    statuses = await db.get_task_statuses_for_date(user_id, target_date)

    date_iso = target_date.isoformat()
    prev_iso = (target_date - timedelta(days=1)).isoformat()
    next_iso = (target_date + timedelta(days=1)).isoformat()

    nav_row = [InlineKeyboardButton(text="⬅️ Oldingi kun", callback_data=f"nav_{prev_iso}")]
    if target_date < today:
        nav_row.append(InlineKeyboardButton(text="Keyingi kun ➡️", callback_data=f"nav_{next_iso}"))

    if target_date == today:
        header_label = f"📋 <b>Bugungi vazifalar ({target_date.strftime('%d.%m.%Y')} — {WEEKDAYS_SHORT[weekday_idx]})</b>"
    elif target_date == today - timedelta(days=1):
        header_label = f"✏️ <b>Kechagi vazifalar ({target_date.strftime('%d.%m.%Y')} — {WEEKDAYS_SHORT[weekday_idx]})</b>"
    else:
        header_label = f"✏️ <b>Arxiv vazifalar ({target_date.strftime('%d.%m.%Y')} — {WEEKDAYS_SHORT[weekday_idx]})</b>"

    nl = chr(10)
    if not tasks:
        text = nl.join([header_label, "", "Bu sana uchun belgilangan vazifalar topilmadi."])
        return text, InlineKeyboardMarkup(inline_keyboard=[nav_row])

    done_count = sum(1 for t in tasks if statuses.get(t['id']) == 'done')
    missed_count = sum(1 for t in tasks if statuses.get(t['id']) == 'missed')
    total_count = len(tasks)

    lines = [
        header_label,
        f"✅ Bajarildi: <b>{done_count}</b> | ❌ Qilinmadi: <b>{missed_count}</b> | Jami: <b>{total_count}</b>",
        ""
    ]

    kb_rows = []
    for t in tasks:
        st = statuses.get(t['id'])
        if st == 'done':
            status_icon = "✅"
        elif st == 'missed':
            status_icon = "❌"
        else:
            status_icon = "⏳"

        lines.append(f"{status_icon} <b>{t['title']}</b> ({t['target_amount']})")

        done_btn_text = "✅ Bajarildi ✓" if st == 'done' else "✅"
        miss_btn_text = "❌ Qilinmadi ✓" if st == 'missed' else "❌ Qilmadim"

        kb_rows.append([
            InlineKeyboardButton(text=f"{t['title'][:18]}:", callback_data=f"noop_{t['id']}"),
            InlineKeyboardButton(text=done_btn_text, callback_data=f"st_{date_iso}_done_{t['id']}"),
            InlineKeyboardButton(text=miss_btn_text, callback_data=f"st_{date_iso}_miss_{t['id']}")
        ])

    kb_rows.append(nav_row)
    return nl.join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows)

MENU_BUTTONS = {
    "✅ Bugungi vazifalar",
    "✏️ Oldingi kunlarni tahrirlash",
    "➕ Vazifa qo'shish",
    "📝 Kunlik yozuv (Eslatma)",
    "📊 Hisobot",
    "🏆 Daraja va Medallar",
    "🤖 Haftalik AI Xulosa",
    "🖼 Oylik Kollaj",
    "📖 Mening 365 kunim (PDF)",
    "📋 Barcha vazifalar",
    "ℹ️ Bot haqida"
}

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await db.register_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.full_name
    )
    nl = chr(10)
    text = nl.join([
        f"Assalomu alaykum, <b>{message.from_user.full_name}</b>! 👋",
        "",
        "Shaxsiy intizom va kunlik natijalar botiga xush kelibsiz:",
        "• <b>✅ Bugungi vazifalar</b> — vazifa yonida <b>✅</b> va <b>❌ Qilmadim</b> tugmalari",
        "• <b>✏️ Oldingi kunlarni tahrirlash</b> — belgilanmay qolgan va avtomatik <i>Qilinmadi</i> bo'lgan kunlarni istalgan vaqtda o'zgartirish",
        "• Har kuni <b>07:00</b> da motivatsiya va kunlik vazifalar ro'yxati",
        "",
        f"👨‍💻 Yaratuvchi: {CREATOR_USERNAME}"
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu())

@router.callback_query(F.data.startswith("noop_"))
async def noop_cb(callback: CallbackQuery):
    await callback.answer("O'ng tomondagi ✅ yoki ❌ Qilmadim tugmasini bosing.")

@router.message(F.text == "ℹ️ Bot haqida")
@router.message(Command("about"))
async def about_bot(message: Message, state: FSMContext):
    await state.clear()
    nl = chr(10)
    text = nl.join([
        "ℹ️ <b>Bot haqida ma'lumot</b>",
        "",
        "Ushbu bot shaxsiy intizom, kunlik odatlarni shakllantirish va yil davomidagi o'sishingizni tahlil qilish uchun yaratilgan.",
        "",
        "<b>Asosiy imkoniyatlari:</b>",
        "• ✅ <b>Kunlik nazorat:</b> Har bir vazifa yonida bir bosishda <i>Bajarildi</i> yoki <i>Qilmadim</i> belgisini qo'yish",
        "• 🔄 <b>Avto-nazorat va Tahrirlash:</b> Kun oxirigacha belgilanmagan vazifalar ertasi kuni avtomatik <i>Qilinmadi</i> deb yoziladi va xohlasangiz ularni keyinroq tahrirlashingiz mumkin",
        "• 🗓 <b>Moslashuvchan jadval:</b> Vazifalarni har kunga yoki haftaning ma'lum kunlariga belgilash",
        "• ⏰ <b>07:00 eslatma:</b> Har kuni tongda motivatsion fikr va bugungi vazifalar ro'yxati",
        "• 📝 <b>Kunlik yozuv:</b> Kun xulosasi, rasm va kayfiyat emoji'sini saqlash",
        "• 📊 <b>Hisobot va Grafik:</b> Kunlik, haftalik, oylik hisobotlar hamda kayfiyat grafigi",
        "• 🤖 <b>AI Murabbiy:</b> Oxirgi 7 kunlik natijalar tahlili va shaxsiy maslahatlar",
        "• 🏆 <b>Medallar va PDF kitob:</b> 7, 30, 100, 365 kunlik medallar, oylik rasmlar kollaji va yil yakunida <i>"Mening 365 kunim"</i> PDF kitobi",
        "",
        f"👨‍💻 <b>Yaratuvchi:</b> {CREATOR_USERNAME}"
    ])
    await message.answer(text, parse_mode="HTML")

# --- 1. BUGUNGI VAZIFALAR VA OLDINGI KUNLARNI TAHRIRLASH ---
@router.message(F.text == "✅ Bugungi vazifalar")
async def show_today_checklist(message: Message, state: FSMContext):
    await state.clear()
    today = datetime.now(TZ).date()
    text, kb = await build_checklist_ui_for_date(message.from_user.id, today)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.message(F.text == "✏️ Oldingi kunlarni tahrirlash")
async def show_yesterday_checklist(message: Message, state: FSMContext):
    await state.clear()
    yesterday = datetime.now(TZ).date() - timedelta(days=1)
    await db.auto_mark_missed_for_date(yesterday)
    text, kb = await build_checklist_ui_for_date(message.from_user.id, yesterday)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("nav_"))
async def navigate_checklist_date(callback: CallbackQuery):
    date_iso = callback.data.split("_")[1]
    target_date = date.fromisoformat(date_iso)
    today = datetime.now(TZ).date()
    if target_date < today:
        await db.auto_mark_missed_for_date(target_date)
    text, kb = await build_checklist_ui_for_date(callback.from_user.id, target_date)
    await callback.answer()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("st_"))
async def handle_task_status_btn(callback: CallbackQuery):
    parts = callback.data.split("_")
    date_iso = parts[1]
    action = parts[2]
    task_id = int(parts[3])
    new_status = "done" if action == "done" else "missed"

    target_date = date.fromisoformat(date_iso)
    user_id = callback.from_user.id

    res_status, streak, new_medals = await db.set_task_status(user_id, task_id, target_date, new_status)
    text, kb = await build_checklist_ui_for_date(user_id, target_date)

    if res_status == "done":
        alert = f"✅ Bajarildi! Seriya: {streak} kun"
    elif res_status == "missed":
        alert = "❌ Qilinmadi deb belgilandi"
    else:
        alert = "⏳ Belgi olib tashlandi"

    await callback.answer(alert)
    if kb:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    if new_medals:
        nl = chr(10)
        await callback.message.answer(
            f"🎊 <b>YANGI MEDAL QO'LGA KIRITILDI!</b>{nl}" + nl.join(new_medals),
            parse_mode="HTML"
        )

# --- 2. VAZIFA QO'SHISH ---
@router.message(F.text == "➕ Vazifa qo'shish")
async def start_add_task(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AddTaskState.waiting_title)
    await message.answer("Yangi vazifa nomini yozing (masalan: <i>Kitob o'qish</i> yoki <i>Turnik</i>):", parse_mode="HTML")

@router.message(AddTaskState.waiting_title, F.text, ~F.text.in_(MENU_BUTTONS))
async def process_task_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(AddTaskState.waiting_target)
    await message.answer("Maqsad miqdorini yozing (masalan: <i>20 bet</i> yoki <i>30 daqiqa</i>):", parse_mode="HTML")

@router.message(AddTaskState.waiting_target, F.text, ~F.text.in_(MENU_BUTTONS))
async def process_task_target(message: Message, state: FSMContext):
    selected = {0, 1, 2, 3, 4, 5, 6}
    await state.update_data(target=message.text, selected_days=list(selected))
    await state.set_state(AddTaskState.waiting_days)
    await message.answer(
        "Ushbu vazifani qaysi kunlari bajarasiz? Kunlarni belgilang va <b>💾 Saqlash</b> tugmasini bosing:",
        parse_mode="HTML",
        reply_markup=days_selector_keyboard(selected)
    )

@router.callback_query(AddTaskState.waiting_days, F.data.startswith("tday_"))
async def select_task_days(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split("_")[1]
    data = await state.get_data()
    selected = set(data.get("selected_days", [0, 1, 2, 3, 4, 5, 6]))

    if action == "all":
        selected = {0, 1, 2, 3, 4, 5, 6}
        await state.update_data(selected_days=list(selected))
        await callback.message.edit_reply_markup(reply_markup=days_selector_keyboard(selected))
        await callback.answer("Har kuni tanlandi")
    elif action == "save":
        if not selected:
            await callback.answer("Kamida bitta kunni tanlang!", show_alert=True)
            return
        days_str = "all" if len(selected) == 7 else ",".join(str(d) for d in sorted(selected))
        await db.add_task(callback.from_user.id, data['title'], data['target'], days_str)
        await state.clear()
        nl = chr(10)
        await callback.message.edit_text(
            f"✅ Vazifa saqlandi!{nl}📌 <b>{data['title']}</b> ({data['target']}){nl}🗓 Jadval: <b>{format_days_label(days_str)}</b>",
            parse_mode="HTML"
        )
    else:
        day_idx = int(action)
        if day_idx in selected:
            selected.remove(day_idx)
        else:
            selected.add(day_idx)
        await state.update_data(selected_days=list(selected))
        await callback.message.edit_reply_markup(reply_markup=days_selector_keyboard(selected))
        await callback.answer()

@router.message(F.text == "📋 Barcha vazifalar")
async def list_all_tasks(message: Message, state: FSMContext):
    await state.clear()
    tasks = await db.get_active_tasks(message.from_user.id)
    if not tasks:
        await message.answer("Hozircha faol vazifalar yo'q. ➕ Vazifa qo'shish tugmasini bosing.")
        return

    kb_rows = []
    lines = ["📋 <b>Barcha faol vazifalaringiz:</b>", ""]
    for idx, t in enumerate(tasks, 1):
        sched = format_days_label(t['days_of_week'])
        lines.append(f"{idx}. <b>{t['title']}</b> ({t['target_amount']}) — <i>{sched}</i>")
        kb_rows.append([InlineKeyboardButton(text=f"❌ O'chirish: {t['title']}", callback_data=f"deltask_{t['id']}")])

    nl = chr(10)
    await message.answer(nl.join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

@router.callback_query(F.data.startswith("deltask_"))
async def delete_task_cb(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    await db.delete_task(task_id, callback.from_user.id)
    await callback.answer("Vazifa o'chirildi!")
    await callback.message.edit_text("🗑 Vazifa ro'yxatdan o'chirildi.")

# --- 3. ALOHIDA KUNLIK YOZUV / ESLATMA ---
@router.message(F.text == "📝 Kunlik yozuv (Eslatma)")
async def start_daily_journal(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(DailyJournalState.waiting_note)
    await message.answer("📝 Bugungi kun haqida bir-ikki gap yozing (xulosa, fikr yoki eslatma):")

@router.message(DailyJournalState.waiting_note, F.text, ~F.text.in_(MENU_BUTTONS))
async def journal_note(message: Message, state: FSMContext):
    await state.update_data(note=message.text)
    await state.set_state(DailyJournalState.waiting_photo)
    await message.answer("Bugungi kundan esdalik rasm yuboring (oy oxirida kollaj tayyorlanadi):", reply_markup=skip_photo_kb())

@router.callback_query(DailyJournalState.waiting_photo, F.data == "skip_photo")
async def skip_journal_photo(callback: CallbackQuery, state: FSMContext):
    await state.update_data(photo_id=None)
    await state.set_state(DailyJournalState.waiting_mood)
    await callback.message.edit_text("Bugungi kayfiyatingizni tanlang:", reply_markup=mood_keyboard())

@router.message(DailyJournalState.waiting_photo, F.photo)
async def journal_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    await state.set_state(DailyJournalState.waiting_mood)
    await message.answer("Ajoyib rasm! Endi bugungi kayfiyatingizni tanlang:", reply_markup=mood_keyboard())

@router.callback_query(DailyJournalState.waiting_mood, F.data.startswith("mood_"))
async def finish_daily_journal(callback: CallbackQuery, state: FSMContext):
    mood = callback.data.split("_")[1]
    data = await state.get_data()
    today = datetime.now(TZ).date()

    streak, new_medals = await db.add_daily_journal(
        user_id=callback.from_user.id,
        log_date=today,
        note=data['note'],
        mood=mood,
        photo_file_id=data.get('photo_id')
    )
    await state.clear()

    nl = chr(10)
    msg = nl.join([
        "📝 <b>Kunlik yozuv saqlandi!</b>",
        "",
        f"💭 Yozuv: <i>{data['note']}</i>",
        f"🎭 Kayfiyat: {mood}",
        f"🔥 Joriy seriya: <b>{streak} kun</b>"
    ])

    if new_medals:
        msg += f"{nl}{nl}🎊 <b>YANGI MEDAL QO'LGA KIRITILDI!</b>{nl}" + nl.join(new_medals)

    await callback.message.edit_text(msg, parse_mode="HTML")

# --- 4. HISOBOT ---
@router.message(F.text == "📊 Hisobot")
async def ask_report(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Qaysi davr uchun hisobot kerak?", reply_markup=report_period_kb())

@router.callback_query(F.data.startswith("rep_"))
async def send_report(callback: CallbackQuery):
    period = callback.data.split("_")[1]
    today = datetime.now(TZ).date()
    user_id = callback.from_user.id

    await db.auto_mark_missed_for_date(today - timedelta(days=1))

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

    lines = [f"<b>{title}</b>", ""]
    for l in logs[-25:]:
        mood_part = f" {l['mood']}" if l['mood'] else ""
        note_part = f" — <i>{l['note']}</i>" if l['note'] else ""
        lines.append(f"• <b>{l['log_date'].strftime('%d.%m')}</b> | {l['task_title']}: {l['done_amount']}{mood_part}{note_part}")

    nl = chr(10)
    await callback.message.edit_text(nl.join(lines), parse_mode="HTML")

    if period == "monthly":
        chart_buf = generate_mood_chart(logs, today.strftime("%B %Y"))
        if chart_buf:
            await callback.message.answer_photo(
                BufferedInputFile(chart_buf.read(), filename="mood_chart.png"),
                caption="📈 Oylik kayfiyat dinamikasi grafigi"
            )

# --- 5. DARAJA VA MEDALLAR ---
@router.message(F.text == "🏆 Daraja va Medallar")
async def show_medals(message: Message, state: FSMContext):
    await state.clear()
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

    nl = chr(10)
    medals_text = nl.join(medals) if medals else "Hali medallar yo'q (7, 30, 100, 365 kunlik seriyalarda ochiladi)"
    text = nl.join([
        "🏆 <b>Sizning Daraja va Yutuqlaringiz</b>",
        "",
        f"🎖 Daraja: <b>{rank}</b>",
        f"🔥 Joriy uzluksiz seriya: <b>{streak} kun</b>",
        f"⭐ Eng uzun seriya: <b>{max_streak} kun</b>",
        "",
        "<b>Qo'lga kiritilgan medallar:</b>",
        medals_text
    ])
    await message.answer(text, parse_mode="HTML")

# --- 6. HAFTALIK AI XULOSA ---
@router.message(F.text == "🤖 Haftalik AI Xulosa")
async def weekly_ai_summary(message: Message, state: FSMContext):
    await state.clear()
    wait_msg = await message.answer("🤖 AI haftalik natijalaringizni tahlil qilmoqda...")
    today = datetime.now(TZ).date()
    await db.auto_mark_missed_for_date(today - timedelta(days=1))
    logs = await db.get_logs_by_range(message.from_user.id, today - timedelta(days=7), today)
    advice = await get_weekly_ai_advice(logs)
    nl = chr(10)
    await wait_msg.edit_text(f"🤖 <b>Haftalik AI Xulosa va Maslahat:</b>{nl}{nl}{advice}", parse_mode="HTML")

# --- 7. OYLIK KOLLAJ ---
@router.message(F.text == "🖼 Oylik Kollaj")
async def monthly_collage(message: Message, state: FSMContext):
    await state.clear()
    wait_msg = await message.answer("🖼 Rasmlardan oylik kollaj tayyorlanmoqda...")
    today = datetime.now(TZ).date()
    logs = await db.get_logs_by_range(message.from_user.id, today.replace(day=1), today)
    photo_ids = [l['photo_file_id'] for l in logs if l['photo_file_id']]

    if not photo_ids:
        await wait_msg.edit_text("Bu oyda hali rasmli yozuvlar yuklanmagan.")
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

# --- 8. MENING 365 KUNIM PDF KITOB ---
@router.message(F.text == "📖 Mening 365 kunim (PDF)")
async def export_365_pdf(message: Message, state: FSMContext):
    await state.clear()
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

# --- 9. HAR KUNI 00:05 AVTO-QILINMADI VA 07:00 MOTIVATSIYA ---
async def midnight_auto_missed_job():
    yesterday = datetime.now(TZ).date() - timedelta(days=1)
    count = await db.auto_mark_missed_for_date(yesterday)
    logging.info(f"Auto-marked {count} unmarked tasks as missed for {yesterday}")

async def morning_motivation_job():
    now_local = datetime.now(TZ)
    today = now_local.date()
    await db.auto_mark_missed_for_date(today - timedelta(days=1))

    users = await db.get_all_users()
    motivations = [
        "Yangi kun — yangi imkoniyat! Bugungi kichik qadamlar ertangi katta g'alabalarni yaratadi.",
        "Intizom — bu kayfiyatga qaramasdan o'z so'zingda turishdir. Boshladik!",
        "Kecha qilgan mehnating bugungi kuchingdir. Rejalarni bajarish vaqti keldi!",
        "Muvaffaqiyat tasodif emas, u har kuni takrorlanadigan kichik odatlar natijasidir.",
        "Bugun o'zingga bergan va'dangni bajar — ertangi o'zing senga rahmat aytadi."
    ]
    weekday_idx = today.weekday()
    quote = motivations[now_local.day % len(motivations)]
    nl = chr(10)

    for u in users:
        tasks = await db.get_tasks_for_date(u['user_id'], today)
        if tasks:
            task_list = nl.join([f"▫️ <b>{t['title']}</b> ({t['target_amount']})" for t in tasks])
        else:
            task_list = "▫️ Bugun uchun vazifa belgilanmagan"

        text = nl.join([
            "☀️ <b>Xayrli tong!</b>",
            "",
            f"💡 <i>"{quote}"</i>",
            "",
            f"📋 <b>Bugungi vazifalar ro'yxati ({WEEKDAYS_SHORT[weekday_idx]}):</b>",
            task_list
        ])
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

    yesterday = datetime.now(TZ).date() - timedelta(days=1)
    await db.auto_mark_missed_for_date(yesterday)

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(midnight_auto_missed_job, "cron", hour=0, minute=5)
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

    nl = chr(10)
    try:
        await bot.set_my_short_description(
            short_description=f"Shaxsiy intizom, kunlik vazifalar va 365 kunlik rivojlanish kundaligi. Yaratuvchi: {CREATOR_USERNAME}"
        )
        await bot.set_my_description(
            description=nl.join([
                "🎯 Shaxsiy intizom va kunlik natijalarni nazorat qiluvchi aqlli yordamchi.",
                "",
                "✅ Kunlik vazifalarni bir bosishda belgilang",
                "⏰ Har kuni soat 07:00 da motivatsiya va rejalar ro'yxati",
                "📊 Kunlik, haftalik, oylik hisobotlar va kayfiyat grafigi",
                "🤖 Haftalik AI tahlil va maslahatlar",
                "🖼 Oylik rasmlar kollaji va yil oxirida 'Mening 365 kunim' PDF kitobi",
                "",
                f"👨‍💻 Yaratuvchi: {CREATOR_USERNAME}"
            ])
        )
    except Exception as e:
        logging.warning(f"Bot description update skipped: {e}")

    logging.info(f"Web server running on port {port}. Starting polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
