import io
import os
from PIL import Image
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
import google.generativeai as genai

MOOD_SCORES = {
    "🤩": 5,
    "😊": 4,
    "😐": 3,
    "😔": 2,
    "😫": 1
}

def generate_mood_chart(logs, month_name: str) -> io.BytesIO:
    dates = []
    scores = []
    for log in logs:
        if log['mood'] in MOOD_SCORES:
            dates.append(log['log_date'].strftime("%d"))
            scores.append(MOOD_SCORES[log['mood']])

    if not dates:
        return None

    plt.figure(figsize=(8, 4))
    plt.plot(dates, scores, marker='o', linestyle='-', color='#2E86C1', linewidth=2.5, markersize=8)
    plt.fill_between(dates, scores, color='#AED6F1', alpha=0.4)
    plt.yticks([1, 2, 3, 4, 5], ["😫 (1)", "😔 (2)", "😐 (3)", "😊 (4)", "🤩 (5)"])
    plt.ylim(0.5, 5.5)
    plt.title(f"{month_name} - Oylik Kayfiyat Dinamikasi", fontsize=13, fontweight='bold', pad=12)
    plt.xlabel("Kunlar", fontsize=10)
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    plt.close()
    buf.seek(0)
    return buf

def create_monthly_collage(image_bytes_list: list) -> io.BytesIO:
    if not image_bytes_list:
        return None

    imgs = []
    for b in image_bytes_list[:16]:
        try:
            im = Image.open(io.BytesIO(b)).convert("RGB")
            im = im.resize((400, 400))
            imgs.append(im)
        except Exception:
            continue

    if not imgs:
        return None

    count = len(imgs)
    cols = 2 if count <= 4 else (3 if count <= 9 else 4)
    rows = (count + cols - 1) // cols

    canvas_w = cols * 410 + 10
    canvas_h = rows * 410 + 10
    collage = Image.new("RGB", (canvas_w, canvas_h), (245, 247, 250))

    for idx, im in enumerate(imgs):
        r = idx // cols
        c = idx % cols
        x = 10 + c * 410
        y = 10 + r * 410
        collage.paste(im, (x, y))

    buf = io.BytesIO()
    collage.save(buf, format="JPEG", quality=90)
    buf.seek(0)
    return buf

def generate_365_pdf_book(user_name: str, year: int, logs: list, stats: dict) -> io.BytesIO:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    c.setFillColor(colors.HexColor("#1B2631"))
    c.rect(0, 0, width, height, fill=1)

    c.setFillColor(colors.HexColor("#F1C40F"))
    c.setFont("Helvetica-Bold", 28)
    c.drawCentredString(width / 2, height - 220, f"MENING {year}-YILIM")

    c.setFillColor(colors.white)
    c.setFont("Helvetica", 18)
    c.drawCentredString(width / 2, height - 260, "365 Kunlik Shaxsiy Rivojlanish Kitobi")

    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(width / 2, height - 340, f"Muallif: {user_name}")

    c.setFont("Helvetica", 12)
    c.setFillColor(colors.HexColor("#BDC3C7"))
    c.drawCentredString(width / 2, height - 380, f"Jami yozuvlar: {len(logs)} | Eng uzun seriya: {stats.get('max_streak', 0)} kun")
    c.drawCentredString(width / 2, 60, "Intizom va Maqsadlar Kundaligi")
    c.showPage()

    y = height - 60
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, "Yillik Xotiralar va Natijalar")
    y -= 30

    for log in logs:
        if y < 100:
            c.showPage()
            y = height - 60

        date_str = log['log_date'].strftime("%Y-%m-%d")
        task = log['task_title'] or "Umumiy kun"
        amount = log['done_amount'] or "-"
        note = log['note'] or ""

        c.setFillColor(colors.HexColor("#2874A6"))
        c.setFont("Helvetica-Bold", 11)
        c.drawString(50, y, f"[{date_str}] {task} — Natija: {amount}")
        y -= 16

        c.setFillColor(colors.HexColor("#2C3E50"))
        c.setFont("Helvetica", 10)
        clean_note = note.encode('latin-1', 'replace').decode('latin-1')
        lines = [clean_note[i:i+85] for i in range(0, len(clean_note), 85)]
        for line in lines[:3]:
            c.drawString(65, y, line)
            y -= 14

        c.setStrokeColor(colors.HexColor("#E5E8E8"))
        c.line(50, y, width - 50, y)
        y -= 16

    c.save()
    buf.seek(0)
    return buf

async def get_weekly_ai_advice(logs: list) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "your_gemini_api_key_here":
        return "⚠️ AI maslahat olish uchun Render sozlamalariga GEMINI_API_KEY qo'shing."

    if not logs:
        return "Haftalik tahlil uchun yetarli ma'lumot topilmadi. Bugundan natijalarni kiritishni boshlang!"

    summary_lines = []
    for l in logs:
        summary_lines.append(f"- {l['log_date']}: {l['task_title']} ({l['done_amount']}), kayfiyat: {l['mood']}, izoh: {l['note']}")

    nl = chr(10)
    prompt = f"""Sen shaxsiy rivojlanish va intizom bo'yicha kuchli murabbiysan. Foydalanuvchining oxirgi 7 kunlik natijalarini tahlil qil va o'zbek tilida qisqa, aniq, ruhlantiruvchi xulosa hamda kelasi hafta uchun 3 ta amaliy maslahat ber:

{nl.join(summary_lines)}"""

    try:
        genai.configure(api_key=api_key)
        for model_name in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]:
            try:
                model = genai.GenerativeModel(model_name)
                response = await model.generate_content_async(prompt)
                if response and response.text:
                    return response.text
            except Exception as inner_e:
                last_err = inner_e
                continue
        return f"AI tahlilda xatolik yuz berdi: {str(last_err)}"
    except Exception as e:
        return f"AI tahlilda xatolik yuz berdi: {str(e)}"
