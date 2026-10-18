"""
Do'kon Telegram boti (aiogram 3.x)
Rollar: Menejer, Kassir, Mijoz

O'rnatish:  pip install aiogram aiosqlite anthropic
Ishga tushirish:
    export BOT_TOKEN="123456:ABC..."
    export ANTHROPIC_API_KEY="sk-ant-..."   # kassir sotuvni matn bilan yozishi uchun (AI)
    python bot.py
"""
import asyncio
import difflib
import json
import logging
import os
import random
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import BaseFilter, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

BOT_TOKEN = "8449631689:AAH4NNzGUufV044kWJBhxMlKgODgb03Uyww"
DB = os.getenv("DB_PATH", "shop.db")
TZ = ZoneInfo("Asia/Tashkent")
AI_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_PASSWORDS = {"manager_password": "manager1234", "cashier_password": "kassir1234"}

try:
    from anthropic import AsyncAnthropic

    ai = AsyncAnthropic() if os.getenv("ANTHROPIC_API_KEY") else None
except ImportError:
    ai = None

router = Router()

# ---------------------------------------------------------------- tugmalar
BTN_MGR = "👨‍💼 Menejer"
BTN_CSH = "🧾 Kassir"
BTN_CLI = "🙋 Mijoz"
BTN_EXIT = "🚪 Chiqish"
BTN_TODAY = "📊 Bugungi ma'lumot"
BTN_PW = "🔑 Parollarni o'zgartirish"
BTN_ADD_DEBT = "➕ Qarzdor qo'shish"
BTN_PAY = "💵 Qarz to'lovi"
BTN_STOCKIN = "📥 Tovar kirimi"
BTN_DEBTS = "📋 Qarzdorlar"
BTN_ITEMS = "📦 Do'kondagi narsalar"
BTN_MYDEBT = "💳 Qarzimni ko'rish"
BTN_WROLE = "👷 Ishchi"
BTN_WORKERS = "👷 Ishchilar"
BTN_LOG = "➕ Ish kiritish"
BTN_MYPAY = "📊 Mening oyligim"
BTN_UNDO = "↩️ Oxirgisini bekor qilish"

ROLE_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_MGR), KeyboardButton(text=BTN_CSH), KeyboardButton(text=BTN_CLI)],
        [KeyboardButton(text=BTN_WROLE)],
    ],
    resize_keyboard=True,
)

ALL_BUTTONS = {
    BTN_MGR, BTN_CSH, BTN_CLI, BTN_WROLE, BTN_EXIT, BTN_TODAY, BTN_PW, BTN_ADD_DEBT, BTN_PAY,
    BTN_STOCKIN, BTN_DEBTS, BTN_ITEMS, BTN_MYDEBT, BTN_WORKERS, BTN_LOG, BTN_MYPAY, BTN_UNDO,
}


class ButtonResetMiddleware(BaseMiddleware):
    """Menyu tugmasi bosilsa, yarim qolgan amal (holat) bekor qilinadi."""

    async def __call__(self, handler, event, data):
        if getattr(event, "text", None) in ALL_BUTTONS:
            state = data.get("state")
            if state is not None:
                await state.clear()
                data["raw_state"] = None  # holat filtri eski qiymatni ishlatmasligi uchun
        return await handler(event, data)


router.message.outer_middleware(ButtonResetMiddleware())


def menu_kb(role: str) -> ReplyKeyboardMarkup:
    if role == "manager":
        rows = [[BTN_TODAY], [BTN_WORKERS], [BTN_PW], [BTN_EXIT]]
    elif role == "cashier":
        rows = [[BTN_ADD_DEBT, BTN_PAY], [BTN_STOCKIN, BTN_DEBTS], [BTN_ITEMS], [BTN_EXIT]]
    elif role == "worker":
        rows = [[BTN_LOG], [BTN_MYPAY, BTN_UNDO], [BTN_EXIT]]
    else:
        rows = [[BTN_MYDEBT], [BTN_ITEMS], [BTN_EXIT]]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True
    )


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def grid(buttons, n=2):
    return [buttons[i:i + n] for i in range(0, len(buttons), n)]


def markup(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


# ---------------------------------------------------------------- baza
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS users(tg_id INTEGER PRIMARY KEY, role TEXT);
            CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, unit TEXT DEFAULT 'metr');
            CREATE TABLE IF NOT EXISTS products(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER, name TEXT, quantity REAL DEFAULT 0, unit TEXT DEFAULT 'metr');
            CREATE TABLE IF NOT EXISTS debtors(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, phone TEXT UNIQUE, amount INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS workers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, pin TEXT, active INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS work_types(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE, price INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS work_logs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_id INTEGER, work_type_id INTEGER, work_name TEXT,
                qty REAL, price INTEGER, day TEXT, month TEXT);
            CREATE TABLE IF NOT EXISTS payouts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch INTEGER, worker_id INTEGER, amount INTEGER, day TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS sales(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER, name TEXT, qty REAL, unit TEXT, day TEXT);
            """
        )
        for stmt in (
            "ALTER TABLE categories ADD COLUMN unit TEXT DEFAULT 'metr'",
            "ALTER TABLE products ADD COLUMN price INTEGER DEFAULT 0",
            "ALTER TABLE products ADD COLUMN photo TEXT",
            "ALTER TABLE users ADD COLUMN worker_id INTEGER",
            "ALTER TABLE work_logs ADD COLUMN product_id INTEGER",
            "ALTER TABLE work_logs ADD COLUMN product_name TEXT",
            "ALTER TABLE work_logs ADD COLUMN payout_id INTEGER",
            "ALTER TABLE work_logs ADD COLUMN used REAL DEFAULT 0",
            "ALTER TABLE work_logs ADD COLUMN used_unit TEXT",
            "ALTER TABLE work_logs ADD COLUMN made_product_id INTEGER",
            "ALTER TABLE work_types ADD COLUMN kind TEXT DEFAULT 'cut'",
            "ALTER TABLE products ADD COLUMN pending INTEGER DEFAULT 0",
            "ALTER TABLE products ADD COLUMN made_by INTEGER",
        ):
            try:  # eski bazalar uchun
                await db.execute(stmt)
            except Exception:
                pass
        cur = await db.execute("SELECT value FROM settings WHERE key='postel_dona_fix'")
        if not await cur.fetchone():
            await db.execute("UPDATE categories SET unit='dona' WHERE lower(name) LIKE '%postel%'")
            await db.execute(
                "UPDATE products SET unit='dona' WHERE category_id IN (SELECT id FROM categories WHERE unit='dona')"
            )
            await db.execute("INSERT INTO settings(key,value) VALUES('postel_dona_fix','1')")
        cur = await db.execute("SELECT value FROM settings WHERE key='wt_kind_fix'")
        if not await cur.fetchone():  # "tikish" nomli ishlar tayyor mahsulot chiqaradigan turga o'tadi
            await db.execute("UPDATE work_types SET kind='make' WHERE lower(name) LIKE '%tik%'")
            await db.execute("INSERT INTO settings(key,value) VALUES('wt_kind_fix','1')")
        for k, v in DEFAULT_PASSWORDS.items():
            await db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        await db.commit()


async def db_exec(sql, params=()):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.lastrowid


async def db_all(sql, params=()):
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        return await cur.fetchall()


async def db_one(sql, params=()):
    rows = await db_all(sql, params)
    return rows[0] if rows else None


async def get_role(tg_id: int):
    r = await db_one("SELECT role FROM users WHERE tg_id=?", (tg_id,))
    return r["role"] if r else None


async def set_role(tg_id: int, role, worker_id=None):
    if role is None:
        await db_exec("DELETE FROM users WHERE tg_id=?", (tg_id,))
    else:
        await db_exec(
            "INSERT OR REPLACE INTO users(tg_id, role, worker_id) VALUES(?,?,?)", (tg_id, role, worker_id)
        )


async def get_worker_id(tg_id: int):
    r = await db_one("SELECT worker_id FROM users WHERE tg_id=?", (tg_id,))
    return r["worker_id"] if r else None


async def get_setting(key: str) -> str:
    r = await db_one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else ""


# ---------------------------------------------------------------- yordamchilar
class RoleFilter(BaseFilter):
    def __init__(self, *roles: str):
        self.roles = roles

    async def __call__(self, event) -> bool:
        return await get_role(event.from_user.id) in self.roles


def money(x) -> str:
    return f"{int(x):,}".replace(",", " ")


def fmt(x) -> str:
    x = float(x)
    return str(int(x)) if x == int(x) else f"{x:.2f}".rstrip("0").rstrip(".")


def norm_phone(text: str):
    d = re.sub(r"\D", "", text)
    if len(d) == 9:
        d = "998" + d
    if len(d) != 12 or not d.startswith("998"):
        return None
    return "+" + d


def parse_money(text: str):
    d = re.sub(r"\D", "", text)
    return int(d) if d else None


def parse_qty(text: str):
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*(\S.*)?$", text)
    if not m:
        return None, None
    return float(m.group(1).replace(",", ".")), (m.group(2) or "").strip() or None


async def edit(c: CallbackQuery, text: str, kb=None):
    if c.message.photo:  # rasmli xabarni matnga o'zgartirib bo'lmaydi
        try:
            await c.message.delete()
        except Exception:
            pass
        await c.message.answer(text, reply_markup=kb)
    else:
        try:
            await c.message.edit_text(text, reply_markup=kb)
        except Exception:
            await c.message.answer(text, reply_markup=kb)
    await c.answer()


async def debts_text() -> str:
    rows = await db_all("SELECT name, phone, amount FROM debtors WHERE amount>0 ORDER BY amount DESC")
    if not rows:
        return "✅ Qarzdorlar yo'q."
    lines = [f"{i}. {r['name']} — {r['phone']} — {money(r['amount'])} so'm" for i, r in enumerate(rows, 1)]
    total = sum(r["amount"] for r in rows)
    return "💰 Qarzi borlar:\n\n" + "\n".join(lines) + f"\n\nJami: {money(total)} so'm"


async def sales_text() -> str:
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    rows = await db_all(
        "SELECT name, unit, SUM(qty) q FROM sales WHERE day=? GROUP BY name, unit", (today,)
    )
    if not rows:
        return "🧾 Bugun hali sotuv yo'q."
    lines = [f"• {r['name']} — {fmt(r['q'])} {r['unit']}" for r in rows]
    return f"🧾 Bugungi sotuvlar ({today}):\n\n" + "\n".join(lines)


# ---------------------------------------------------------------- bo'lim / mahsulot menyulari
# mode: "v" = ko'rish, "i" = kassir kirim qo'shishi
async def cats_view(mode: str, role: str):
    rows = await db_all("SELECT id, name, unit FROM categories ORDER BY name")
    kb = grid([btn(f"{r['name']} ({r['unit']})", f"cat:{mode}:{r['id']}") for r in rows])
    if role == "manager" and mode == "v":
        kb.append([btn("➕ Bo'lim qo'shish", "addcat")])
    text = "📦 Bo'limni tanlang:" if rows else "Hozircha bo'limlar yo'q."
    return text, markup(kb)


async def prods_view(mode: str, cid: int, role: str):
    cat = await db_one("SELECT name, unit FROM categories WHERE id=?", (cid,))
    if not cat:
        return "Bo'lim topilmadi.", markup([[btn("⬅️ Orqaga", f"cats:{mode}")]])
    only_priced = " AND COALESCE(pending,0)=0" if role == "client" else ""
    rows = await db_all(
        f"SELECT id, name FROM products WHERE category_id=?{only_priced} ORDER BY name", (cid,)
    )
    kb = grid([btn(r["name"], f"prod:{mode}:{r['id']}") for r in rows])
    if role == "manager" and mode == "v":
        kb.append([btn("➕ Mahsulot qo'shish", f"addprod:{cid}")])
        kb.append([btn("📏 Birlikni almashtirish", f"unit:{cid}"), btn("🗑 Bo'limni o'chirish", f"del:cat:{cid}")])
    kb.append([btn("⬅️ Orqaga", f"cats:{mode}")])
    head = f"📂 {cat['name']} (hisob: {cat['unit']})"
    text = f"{head}\nMahsulotni tanlang:" if rows else f"{head}\nBu bo'limda mahsulot yo'q."
    return text, markup(kb)


# ---------------------------------------------------------------- holatlar
class Login(StatesGroup):
    password = State()


class ChangePw(StatesGroup):
    new = State()


class AddCat(StatesGroup):
    name = State()
    unit = State()


class AddProd(StatesGroup):
    name = State()
    qty = State()
    price = State()
    photo = State()


class SetPhoto(StatesGroup):
    photo = State()


class SetPrice(StatesGroup):
    price = State()


class SetQty(StatesGroup):
    qty = State()


class StockIn(StatesGroup):
    qty = State()


class AddDebt(StatesGroup):
    name = State()
    phone = State()
    amount = State()


class Pay(StatesGroup):
    phone = State()
    amount = State()


class CheckDebt(StatesGroup):
    phone = State()


class WorkerLogin(StatesGroup):
    pin = State()


class AddWorker(StatesGroup):
    name = State()


class AddWorkType(StatesGroup):
    name = State()
    price = State()
    kind = State()


class SetWorkPrice(StatesGroup):
    price = State()


class LogWork(StatesGroup):
    pick = State()
    used = State()
    qty = State()


class MakeWork(StatesGroup):
    what = State()
    size = State()
    name = State()
    qty = State()
    photo = State()


# ---------------------------------------------------------------- /start va rol tanlash
@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await set_role(m.from_user.id, None)
    await m.answer("Assalomu alaykum! Kerakli bo'limni tanlang:", reply_markup=ROLE_KB)


@router.message(F.text == BTN_EXIT)
async def exit_role(m: Message, state: FSMContext):
    await state.clear()
    await set_role(m.from_user.id, None)
    await m.answer("Kerakli bo'limni tanlang:", reply_markup=ROLE_KB)


@router.message(F.text.in_({BTN_MGR, BTN_CSH}))
async def ask_password(m: Message, state: FSMContext):
    role = "manager" if m.text == BTN_MGR else "cashier"
    await state.clear()
    await state.set_state(Login.password)
    await state.update_data(role=role)
    await m.answer("🔑 Parolni kiriting:", reply_markup=ReplyKeyboardRemove())


@router.message(Login.password, F.text)
async def check_password(m: Message, state: FSMContext):
    role = (await state.get_data())["role"]
    real = await get_setting(f"{role}_password")
    try:
        await m.delete()  # parol chatda qolmasligi uchun
    except Exception:
        pass
    if m.text.strip() != real:
        await m.answer("❌ Parol noto'g'ri. Qayta kiriting yoki /start bosing.")
        return
    await state.clear()
    await set_role(m.from_user.id, role)
    title = "Menejer" if role == "manager" else "Kassir"
    await m.answer(f"✅ Xush kelibsiz, {title}!", reply_markup=menu_kb(role))


@router.message(F.text == BTN_CLI)
async def client_login(m: Message, state: FSMContext):
    await state.clear()
    await set_role(m.from_user.id, "client")
    await m.answer("Assalomu alaykum! Kerakli bo'limni tanlang:", reply_markup=menu_kb("client"))


# ================================================================ ISHCHILAR VA OYLIK HISOBI
MONTHS_UZ = [
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
]


def cur_month() -> str:
    return datetime.now(TZ).strftime("%Y-%m")


def shift_month(month: str, delta: int) -> str:
    y, mo = month.split("-")
    idx = int(y) * 12 + int(mo) - 1 + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def month_title(month: str) -> str:
    y, mo = month.split("-")
    return f"{y}-yil {MONTHS_UZ[int(mo) - 1]}"


def nav_row(prefix: str, month: str):
    prev_m = shift_month(month, -1)
    row = [btn(f"⬅️ {month_title(prev_m)}", f"{prefix}{prev_m}")]
    if month < cur_month():
        next_m = shift_month(month, 1)
        row.append(btn(f"{month_title(next_m)} ➡️", f"{prefix}{next_m}"))
    return row


async def new_pin() -> str:
    while True:
        pin = str(random.randint(10000, 99999))
        if not await db_one("SELECT id FROM workers WHERE pin=?", (pin,)):
            return pin


async def month_total(wid: int, month: str) -> float:
    r = await db_one(
        "SELECT COALESCE(SUM(qty*price),0) t FROM work_logs WHERE worker_id=? AND month=?", (wid, month)
    )
    return r["t"]


async def unpaid_total(wid: int) -> float:
    r = await db_one(
        "SELECT COALESCE(SUM(qty*price),0) t FROM work_logs WHERE worker_id=? AND payout_id IS NULL", (wid,)
    )
    return r["t"]


async def breakdown_by(wid: int, cond: str, params: tuple, title: str) -> str:
    w = await db_one("SELECT name FROM workers WHERE id=?", (wid,))
    head = f"👷 {w['name'] if w else '—'}\n{title}\n\n"
    rows = await db_all(
        "SELECT work_name, COALESCE(product_name,'') AS product_name, price, SUM(qty) q, "
        "COALESCE(SUM(used),0) AS used, COALESCE(MAX(used_unit),'') AS used_unit FROM work_logs "
        f"WHERE worker_id=? AND {cond} GROUP BY work_name, product_name, price ORDER BY work_name, product_name",
        (wid, *params),
    )
    if not rows:
        return head + "Kiritilgan ish yo'q."
    lines, total = [], 0
    for r in rows:
        s_ = r["q"] * r["price"]
        total += s_
        what = f"{r['work_name']} — {r['product_name']}" if r["product_name"] else r["work_name"]
        extra = f" (material: {fmt(r['used'])} {r['used_unit']})" if r["used"] else ""
        lines.append(f"• {what}: {fmt(r['q'])} ta × {money(r['price'])} = {money(s_)} so'm{extra}")
    return head + "\n".join(lines) + f"\n\n💰 Jami oylik: {money(total)} so'm"


async def breakdown_text(wid: int, month: str) -> str:
    return await breakdown_by(wid, "month=?", (month,), f"📅 {month_title(month)}")


async def unpaid_text(wid: int) -> str:
    return await breakdown_by(wid, "payout_id IS NULL", (), "🕒 Joriy hisob (hali hisob-kitob qilinmagan)")


def workers_menu_kb():
    return markup([
        [btn("💰 Joriy oylik hisobi", "wcur")],
        [btn("📅 Oylar bo'yicha", f"wpay:{cur_month()}"), btn("📜 Hisob-kitoblar tarixi", "whist")],
        [btn("👥 Ishchilar", "wlist"), btn("➕ Ishchi qo'shish", "waddw")],
        [btn("🛠 Ish turlari va narxlari", "wtypes")],
        [btn("🆕 Narx kutayotgan mahsulotlar", "wpend")],
    ])


WORKERS_MENU_TEXT = "👷 Ishchilar va oylik hisobi:"


# ---------------- ishchi kirishi (kod bilan)
@router.message(F.text == BTN_WROLE)
async def worker_ask_pin(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(WorkerLogin.pin)
    await m.answer("🔑 Shaxsiy kodingizni kiriting (menejer bergan raqam):", reply_markup=ReplyKeyboardRemove())


@router.message(WorkerLogin.pin, F.text)
async def worker_check_pin(m: Message, state: FSMContext):
    pin = m.text.strip()
    try:
        await m.delete()
    except Exception:
        pass
    w = await db_one("SELECT id, name FROM workers WHERE pin=? AND active=1", (pin,))
    if not w:
        await m.answer("❌ Kod noto'g'ri. Qayta kiriting yoki /start bosing.")
        return
    await state.clear()
    await set_role(m.from_user.id, "worker", w["id"])
    await m.answer(f"✅ Xush kelibsiz, {w['name']}!", reply_markup=menu_kb("worker"))


# ---------------- ishchi: ish kiritish
@router.message(RoleFilter("worker"), F.text == BTN_LOG)
async def worker_log(m: Message, state: FSMContext):
    await state.clear()
    rows = await db_all("SELECT id, name, price FROM work_types ORDER BY name")
    if not rows:
        await m.answer("Hozircha ish turlari yo'q. Menejerga ayting.")
        return
    kb = [[btn(f"{r['name']} — {money(r['price'])} so'm", f"wlog:{r['id']}")] for r in rows]
    await m.answer("Qaysi ishni qildingiz?", reply_markup=markup(kb))


async def log_cats_kb():
    rows = await db_all("SELECT id, name FROM categories ORDER BY name")
    return grid([btn(r["name"], f"wlc:{r['id']}") for r in rows])


async def save_log(wid, t, p, qty, used=0.0, made_pid=None, pname=None):
    """Material ayirish va yozuvni bitta tranzaksiyada saqlaydi. Material yetmasa False."""
    now = datetime.now(TZ)
    async with aiosqlite.connect(DB) as db:
        if used and p:
            cur = await db.execute(
                "UPDATE products SET quantity=quantity-? WHERE id=? AND quantity>=?", (used, p["id"], used)
            )
            if cur.rowcount == 0:
                return False
        await db.execute(
            "INSERT INTO work_logs(worker_id, work_type_id, work_name, product_id, product_name, qty, price, "
            "day, month, used, used_unit, made_product_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                wid, t["id"], t["name"], p["id"] if p else None, pname or (p["name"] if p else None), qty,
                t["price"], now.strftime("%Y-%m-%d"), now.strftime("%Y-%m"),
                used if (used and p) else 0, p["unit"] if (used and p) else None, made_pid,
            ),
        )
        await db.commit()
    return True


@router.callback_query(RoleFilter("worker"), F.data.startswith("wlog:"))
async def worker_log_pick(c: CallbackQuery, state: FSMContext):
    t = await db_one("SELECT id, name, kind FROM work_types WHERE id=?", (int(c.data.split(":")[1]),))
    if not t:
        return await c.answer("Topilmadi", show_alert=True)
    kind = t["kind"] or "cut"
    await state.update_data(tid=t["id"], pid=None, used=0)
    if kind == "make":  # tikish: nima tayyorlandi
        await state.set_state(MakeWork.what)
        await edit(c, f"«{t['name']}» — nima tayyorladingiz? Qo'lda yozing (masalan: postel):")
        return
    if kind == "simple":
        await state.set_state(LogWork.qty)
        await edit(c, f"«{t['name']}» — nechta qildingiz? (son yozing)")
        return
    kb = await log_cats_kb()  # bichish: qaysi materialdan, necha metr ketdi
    if not kb:  # omborda hali material yo'q bo'lsa, materialsiz kiritiladi
        await state.set_state(LogWork.qty)
        await edit(c, f"«{t['name']}» — nechta qildingiz? (son yozing)")
        return
    await state.set_state(LogWork.pick)
    await edit(c, f"«{t['name']}» — qaysi materialdan? Bo'limni tanlang:", markup(kb))


@router.callback_query(RoleFilter("worker"), F.data == "wlcats")
async def worker_log_cats(c: CallbackQuery):
    await edit(c, "Qaysi materialdan? Bo'limni tanlang:", markup(await log_cats_kb()))


@router.callback_query(RoleFilter("worker"), F.data.startswith("wlc:"))
async def worker_log_cat(c: CallbackQuery):
    cid = int(c.data.split(":")[1])
    cat = await db_one("SELECT name FROM categories WHERE id=?", (cid,))
    rows = await db_all("SELECT id, name FROM products WHERE category_id=? ORDER BY name", (cid,))
    if not cat or not rows:
        return await c.answer("Bu bo'limda material yo'q", show_alert=True)
    kb = grid([btn(r["name"], f"wlp:{r['id']}") for r in rows])
    kb.append([btn("⬅️ Orqaga", "wlcats")])
    await edit(c, f"📂 {cat['name']}\nQaysi material?", markup(kb))


@router.callback_query(RoleFilter("worker"), F.data.startswith("wlp:"))
async def worker_log_product(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    t = await db_one("SELECT name FROM work_types WHERE id=?", (data.get("tid"),))
    p = await db_one("SELECT id, name, quantity, unit FROM products WHERE id=?", (int(c.data.split(":")[1]),))
    if not t or not p:
        return await c.answer("Qaytadan boshlang (➕ Ish kiritish)", show_alert=True)
    await state.update_data(pid=p["id"])
    await state.set_state(LogWork.used)
    await edit(
        c,
        f"«{t['name']}» — «{p['name']}» dan necha {p['unit']} material ketdi?\n"
        f"(omborda: {fmt(p['quantity'])} {p['unit']})\nSon yozing:",
    )


@router.message(LogWork.used, F.text)
async def worker_log_used(m: Message, state: FSMContext):
    used, _ = parse_qty(m.text)
    if used is None or used <= 0:
        await m.answer("Faqat son yozing, masalan: 12 yoki 2.5")
        return
    data = await state.get_data()
    p = await db_one("SELECT name, quantity, unit FROM products WHERE id=?", (data.get("pid"),))
    if not p:
        await state.clear()
        await m.answer("Xatolik: qaytadan urinib ko'ring.")
        return
    if used > p["quantity"]:
        await m.answer(
            f"⚠️ Omborda «{p['name']}» dan faqat {fmt(p['quantity'])} {p['unit']} bor.\n"
            "Kamroq yozing yoki menejerga ayting."
        )
        return
    await state.update_data(used=used)
    await state.set_state(LogWork.qty)
    await m.answer("Nechta (dona) qildingiz? (oylik shu songa qarab hisoblanadi)")


@router.message(LogWork.qty, F.text)
async def worker_log_qty(m: Message, state: FSMContext):
    qty, _ = parse_qty(m.text)
    if qty is None or qty <= 0:
        await m.answer("Faqat son yozing, masalan: 20")
        return
    wid = await get_worker_id(m.from_user.id)
    data = await state.get_data()
    t = await db_one("SELECT id, name, price FROM work_types WHERE id=?", (data.get("tid"),))
    p = None
    if data.get("pid"):
        p = await db_one("SELECT id, name, quantity, unit FROM products WHERE id=?", (data["pid"],))
    if not wid or not t:
        await state.clear()
        await m.answer("Xatolik: qaytadan urinib ko'ring.")
        return
    used = float(data.get("used") or 0) if p else 0.0
    if not await save_log(wid, t, p, qty, used):
        await state.clear()
        await m.answer("⚠️ Omborda material yetarli emas (qoldiq o'zgargan). Qaytadan kiriting.")
        return
    await state.clear()
    total = await unpaid_total(wid)
    what = f"{t['name']} — {p['name']}" if p else t["name"]
    text = f"✅ {what}: {fmt(qty)} ta × {money(t['price'])} = {money(qty * t['price'])} so'm"
    if used and p:
        text += f"\n🧵 Ombordan ayrildi: {fmt(used)} {p['unit']} (qoldi: {fmt(p['quantity'] - used)} {p['unit']})"
    text += f"\n💰 Joriy hisob: {money(total)} so'm"
    await m.answer(text)


# ---------------- tikish: tayyor mahsulot yaratish
@router.message(MakeWork.what, F.text)
async def make_what(m: Message, state: FSMContext):
    what = m.text.strip()
    if not what or len(what) > 40:
        await m.answer("Qisqa nom yozing, masalan: postel")
        return
    await state.update_data(what=what)
    await state.set_state(MakeWork.size)
    kb = [[btn("1 kishilik", "mksize:1"), btn("2 kishilik", "mksize:2")]]
    await m.answer(f"«{what}» necha kishilik?", reply_markup=markup(kb))


async def make_size_done(msg: Message, state: FSMContext, size: str):
    what = (await state.get_data())["what"]
    await state.update_data(size=size)
    await state.set_state(MakeWork.name)
    await msg.answer(f"{what.capitalize()} ({size} kishilik) — nomini yozing (masalan: Anor):")


@router.callback_query(MakeWork.size, F.data.startswith("mksize:"))
async def make_size(c: CallbackQuery, state: FSMContext):
    await make_size_done(c.message, state, c.data.split(":")[1])
    await c.answer()


@router.message(MakeWork.size, F.text)
async def make_size_text(m: Message, state: FSMContext):
    if m.text.strip() in ("1", "2"):
        await make_size_done(m, state, m.text.strip())
    else:
        await m.answer("Tugmalardan birini tanlang: 1 yoki 2 kishilik.")


@router.message(MakeWork.name, F.text)
async def make_name(m: Message, state: FSMContext):
    name = m.text.strip()
    if not name or len(name) > 60:
        await m.answer("Qisqa nom yozing, masalan: Anor")
        return
    await state.update_data(name=name)
    await state.set_state(MakeWork.qty)
    await m.answer("Nechta (dona) tikdingiz?")


@router.message(MakeWork.qty, F.text)
async def make_qty(m: Message, state: FSMContext):
    qty, _ = parse_qty(m.text)
    if qty is None or qty <= 0:
        await m.answer("Faqat son yozing, masalan: 5")
        return
    await state.update_data(qty=qty)
    await state.set_state(MakeWork.photo)
    kb = [[btn("⏭ Rasmsiz davom etish", "mkskip")]]
    await m.answer("📷 Tayyor mahsulot rasmini yuboring (majburiy emas):", reply_markup=markup(kb))


async def finish_make(msg: Message, state: FSMContext, tg_id: int, photo):
    d = await state.get_data()
    wid = await get_worker_id(tg_id)
    t = await db_one("SELECT id, name, price FROM work_types WHERE id=?", (d.get("tid"),))
    if not wid or not t:
        await state.clear()
        await msg.answer("Xatolik: qaytadan urinib ko'ring.")
        return
    what, size, qty = d["what"], d["size"], float(d["qty"])
    name = d["name"].strip()[:1].upper() + d["name"].strip()[1:]
    base = what.strip()[:1].upper() + what.strip()[1:]
    pname = f"{name} ({size} kishilik)"
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        cats = await (await db.execute("SELECT id, name, unit FROM categories")).fetchall()
        cat_id = cat_title = None
        for cand in (base, f"{base} (tayyor)", "Tayyor mahsulotlar"):  # bo'lim bor bo'lsa shunga, yo'q bo'lsa yangi
            ex = next((c for c in cats if c["name"].casefold() == cand.casefold()), None)
            if ex is None:
                cur = await db.execute("INSERT INTO categories(name, unit) VALUES(?, 'dona')", (cand,))
                cat_id, cat_title = cur.lastrowid, cand
                break
            if ex["unit"] == "dona" or cand == "Tayyor mahsulotlar":
                cat_id, cat_title = ex["id"], ex["name"]
                break
        prods = await (
            await db.execute(
                "SELECT id, name, price FROM products WHERE category_id=?", (cat_id,)
            )
        ).fetchall()
        ex = next((p for p in prods if p["name"].casefold() == pname.casefold()), None)
        if ex:
            pid = ex["id"]
            pname = ex["name"]  # hisobotda bir xil nom chiqishi uchun
            await db.execute(
                "UPDATE products SET quantity=quantity+?, photo=COALESCE(photo, ?) WHERE id=?", (qty, photo, pid)
            )
            need_price = not (ex["price"] or 0)
            if need_price:
                await db.execute("UPDATE products SET pending=1 WHERE id=?", (pid,))
        else:
            cur = await db.execute(
                "INSERT INTO products(category_id, name, quantity, unit, price, photo, pending, made_by) "
                "VALUES(?,?,?,?,0,?,1,?)",
                (cat_id, pname, qty, "dona", photo, wid),
            )
            pid, need_price = cur.lastrowid, True
        now = datetime.now(TZ)
        await db.execute(
            "INSERT INTO work_logs(worker_id, work_type_id, work_name, product_id, product_name, qty, price, "
            "day, month, used, used_unit, made_product_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (wid, t["id"], t["name"], None, pname, qty, t["price"], now.strftime("%Y-%m-%d"),
             now.strftime("%Y-%m"), 0, None, pid),
        )
        await db.commit()
    await state.clear()
    total = await unpaid_total(wid)
    text = (
        f"✅ {t['name']}: {cat_title} «{pname}» × {fmt(qty)} dona\n"
        f"💰 {fmt(qty)} × {money(t['price'])} = {money(qty * t['price'])} so'm\n"
    )
    if need_price:
        text += "📤 Menejerga yuborildi, narxini u qo'yadi.\n"
    text += f"💰 Joriy hisob: {money(total)} so'm"
    await msg.answer(text)
    if need_price:  # menejerga xabar: narx qo'yish uchun
        w = await db_one("SELECT name FROM workers WHERE id=?", (wid,))
        note = f"🆕 {w['name']} tikdi: {cat_title} «{pname}» — {fmt(qty)} dona.\nNarxi hali qo'yilmagan."
        kb = markup([[btn("💲 Narx qo'yish", f"setprice:{pid}")]])
        photo_id = photo or (await db_one("SELECT photo FROM products WHERE id=?", (pid,)))["photo"]
        for r in await db_all("SELECT tg_id FROM users WHERE role='manager'"):
            try:
                if photo_id:
                    await msg.bot.send_photo(r["tg_id"], photo_id, caption=note, reply_markup=kb)
                else:
                    await msg.bot.send_message(r["tg_id"], note, reply_markup=kb)
            except Exception:
                logging.exception("Menejerga xabar yuborilmadi")


@router.message(MakeWork.photo, F.photo)
async def make_photo(m: Message, state: FSMContext):
    await finish_make(m, state, m.from_user.id, m.photo[-1].file_id)


@router.callback_query(MakeWork.photo, F.data == "mkskip")
async def make_skip(c: CallbackQuery, state: FSMContext):
    await finish_make(c.message, state, c.from_user.id, None)
    await c.answer()


@router.message(MakeWork.photo)
async def make_photo_hint(m: Message):
    await m.answer("Rasm yuboring (siqilgan rasm sifatida) yoki «Rasmsiz davom etish» tugmasini bosing.")


# ---------------- ishchi: oylik va bekor qilish
@router.message(RoleFilter("worker"), F.text == BTN_MYPAY)
async def worker_mypay(m: Message, state: FSMContext):
    await state.clear()
    wid = await get_worker_id(m.from_user.id)
    await m.answer(await unpaid_text(wid), reply_markup=markup([[btn("📅 Oylar bo'yicha", f"wmy:{cur_month()}")]]))


@router.callback_query(RoleFilter("worker"), F.data == "wmycur")
async def worker_mypay_cur(c: CallbackQuery):
    wid = await get_worker_id(c.from_user.id)
    await edit(c, await unpaid_text(wid), markup([[btn("📅 Oylar bo'yicha", f"wmy:{cur_month()}")]]))


@router.callback_query(RoleFilter("worker"), F.data.startswith("wmy:"))
async def worker_mypay_month(c: CallbackQuery):
    wid = await get_worker_id(c.from_user.id)
    month = c.data.split(":")[1]
    kb = [nav_row("wmy:", month), [btn("⬅️ Joriy hisob", "wmycur")]]
    await edit(c, await breakdown_text(wid, month), markup(kb))


@router.message(RoleFilter("worker"), F.text == BTN_UNDO)
async def worker_undo(m: Message, state: FSMContext):
    await state.clear()
    wid = await get_worker_id(m.from_user.id)
    last = await db_one(
        "SELECT * FROM work_logs WHERE worker_id=? AND payout_id IS NULL ORDER BY id DESC LIMIT 1", (wid,)
    )
    if not last:
        await m.answer("Bekor qilinadigan ish yo'q.")
        return
    text = f"Oxirgi kiritilgan ish:\n{last['work_name']}"
    if last["product_name"]:
        text += f" — {last['product_name']}"
    text += f": {fmt(last['qty'])} ta ({last['day']})"
    if (last["used"] or 0) > 0:
        text += f"\n🧵 {fmt(last['used'])} {last['used_unit'] or ''} material omborga qaytariladi."
    if last["made_product_id"]:
        text += "\n📦 Tayyor mahsulot soni ham kamayadi."
    kb = [[btn("✅ Ha, bekor qilish", f"wundo:{last['id']}"), btn("❌ Yo'q", "wno")]]
    await m.answer(text + "\n\nBekor qilinsinmi?", reply_markup=markup(kb))


@router.callback_query(RoleFilter("worker"), F.data.startswith("wundo:"))
async def worker_undo_ok(c: CallbackQuery):
    wid = await get_worker_id(c.from_user.id)
    lid = int(c.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM work_logs WHERE id=? AND worker_id=? AND payout_id IS NULL", (lid, wid)
        )
        log = await cur.fetchone()
        if not log:
            return await edit(c, "Bu ish topilmadi yoki allaqachon hisob-kitob qilingan.")
        if (log["used"] or 0) > 0 and log["product_id"]:
            await db.execute(
                "UPDATE products SET quantity=quantity+? WHERE id=?", (log["used"], log["product_id"])
            )
        if log["made_product_id"]:
            await db.execute(
                "UPDATE products SET quantity=MAX(quantity-?,0) WHERE id=?", (log["qty"], log["made_product_id"])
            )
            await db.execute(
                "DELETE FROM products WHERE id=? AND pending=1 AND quantity<=0", (log["made_product_id"],)
            )
        await db.execute("DELETE FROM work_logs WHERE id=?", (lid,))
        await db.commit()
    await edit(c, "↩️ Bekor qilindi.")


@router.callback_query(RoleFilter("worker"), F.data == "wno")
async def worker_undo_no(c: CallbackQuery):
    await edit(c, "Bekor qilinmadi.")


# ---------------- menejer: ishchilar menyusi
@router.message(RoleFilter("manager"), F.text == BTN_WORKERS)
async def workers_btn(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(WORKERS_MENU_TEXT, reply_markup=workers_menu_kb())


@router.callback_query(RoleFilter("manager"), F.data == "wmenu")
async def workers_menu_cb(c: CallbackQuery):
    await edit(c, WORKERS_MENU_TEXT, workers_menu_kb())


# --- oylik hisobi
@router.callback_query(RoleFilter("manager"), F.data.startswith("wpay:"))
async def workers_pay(c: CallbackQuery):
    month = c.data.split(":")[1]
    rows = await db_all(
        "SELECT w.id, w.name, COALESCE(SUM(l.qty*l.price),0) AS total "
        "FROM workers w LEFT JOIN work_logs l ON l.worker_id=w.id AND l.month=? "
        "WHERE w.active=1 OR l.id IS NOT NULL GROUP BY w.id ORDER BY total DESC",
        (month,),
    )
    lines = [f"{i}. {r['name']} — {money(r['total'])} so'm" for i, r in enumerate(rows, 1)]
    total = sum(r["total"] for r in rows)
    text = f"💰 {month_title(month)} — oylik hisobi\n\n"
    text += "\n".join(lines) if rows else "Hozircha ishchilar yo'q."
    text += f"\n\nJami: {money(total)} so'm"
    kb = grid([btn(r["name"], f"wbrk:{r['id']}:{month}") for r in rows])
    kb.append(nav_row("wpay:", month))
    kb.append([btn("⬅️ Orqaga", "wmenu")])
    await edit(c, text, markup(kb))


@router.callback_query(RoleFilter("manager"), F.data.startswith("wbrk:"))
async def worker_breakdown(c: CallbackQuery):
    _, wid, month = c.data.split(":")
    kb = [nav_row(f"wbrk:{wid}:", month), [btn("⬅️ Orqaga", f"wpay:{month}")]]
    await edit(c, await breakdown_text(int(wid), month), markup(kb))


# --- joriy hisob va "hisoblab yopish"
async def current_rows():
    return await db_all(
        "SELECT w.id, w.name, COALESCE(SUM(l.qty*l.price),0) AS total "
        "FROM workers w LEFT JOIN work_logs l ON l.worker_id=w.id AND l.payout_id IS NULL "
        "WHERE w.active=1 OR l.id IS NOT NULL GROUP BY w.id ORDER BY total DESC"
    )


def report_lines(rows):
    return [f"{i}. {r['name']} — {money(r['total'])} so'm" for i, r in enumerate(rows, 1)]


@router.callback_query(RoleFilter("manager"), F.data == "wcur")
async def workers_current(c: CallbackQuery):
    rows = await current_rows()
    last = await db_one("SELECT MAX(day) d FROM payouts")
    since = f"Oxirgi hisob-kitob: {last['d']}" if last and last["d"] else "Hali hisob-kitob qilinmagan"
    text = f"💰 Joriy oylik hisobi\n{since}\n\n"
    text += "\n".join(report_lines(rows)) if rows else "Hozircha ishchilar yo'q."
    text += f"\n\nJami: {money(sum(r['total'] for r in rows))} so'm"
    kb = grid([btn(r["name"], f"wcb:{r['id']}") for r in rows])
    kb.append([btn("🔄 Oylikni hisoblab yopish", "wsettle")])
    kb.append([btn("⬅️ Orqaga", "wmenu")])
    await edit(c, text, markup(kb))


@router.callback_query(RoleFilter("manager"), F.data.startswith("wcb:"))
async def worker_current_breakdown(c: CallbackQuery):
    wid = int(c.data.split(":")[1])
    await edit(c, await unpaid_text(wid), markup([[btn("⬅️ Orqaga", "wcur")]]))


@router.callback_query(RoleFilter("manager"), F.data == "wsettle")
async def settle_ask(c: CallbackQuery):
    rows = [r for r in await current_rows() if r["total"] > 0]
    if not rows:
        return await c.answer("Hisoblanadigan oylik yo'q.", show_alert=True)
    text = (
        "🔄 Oylikni hisoblab yopasizmi?\n\n" + "\n".join(report_lines(rows))
        + f"\n\nJami: {money(sum(r['total'] for r in rows))} so'm\n\n"
        "Tasdiqlasangiz, bu summalar ishchilarga to'langan deb tarixga yoziladi va joriy hisob noldan boshlanadi."
    )
    kb = [[btn("✅ Ha, hisoblab yopish", "wsettleok")], [btn("❌ Yo'q", "wcur")]]
    await edit(c, text, markup(kb))


async def settle_all():
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT w.id, w.name, SUM(l.qty*l.price) AS total FROM workers w "
            "JOIN work_logs l ON l.worker_id=w.id AND l.payout_id IS NULL "
            "GROUP BY w.id HAVING total > 0 ORDER BY w.name"
        )
        rows = await cur.fetchall()
        if not rows:
            return None, []
        cur = await db.execute("SELECT COALESCE(MAX(batch),0)+1 FROM payouts")
        batch = (await cur.fetchone())[0]
        now = datetime.now(TZ)
        day, created = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M:%S")
        for r in rows:
            cur = await db.execute(
                "INSERT INTO payouts(batch, worker_id, amount, day, created) VALUES(?,?,?,?,?)",
                (batch, r["id"], int(round(r["total"])), day, created),
            )
            await db.execute(
                "UPDATE work_logs SET payout_id=? WHERE worker_id=? AND payout_id IS NULL", (cur.lastrowid, r["id"])
            )
        await db.commit()
        return day, rows


@router.callback_query(RoleFilter("manager"), F.data == "wsettleok")
async def settle_ok(c: CallbackQuery):
    day, rows = await settle_all()
    if not rows:
        return await c.answer("Hisoblanadigan oylik yo'q.", show_alert=True)
    text = (
        f"✅ Oyliklar hisoblandi ({day}):\n\n" + "\n".join(report_lines(rows))
        + f"\n\nJami: {money(sum(r['total'] for r in rows))} so'm\n\nJoriy hisob noldan boshlandi."
    )
    await edit(c, text, markup([[btn("📜 Tarix", "whist")], [btn("⬅️ Orqaga", "wmenu")]]))


@router.callback_query(RoleFilter("manager"), F.data == "whist")
async def settle_history(c: CallbackQuery):
    rows = await db_all(
        "SELECT batch, MAX(day) AS day, SUM(amount) AS total, COUNT(*) AS n FROM payouts "
        "GROUP BY batch ORDER BY batch DESC LIMIT 12"
    )
    kb = [[btn(f"{r['day']} — {money(r['total'])} so'm ({r['n']} ishchi)", f"whb:{r['batch']}")] for r in rows]
    kb.append([btn("⬅️ Orqaga", "wmenu")])
    await edit(c, "📜 Hisob-kitoblar tarixi:" if rows else "Hali hisob-kitob qilinmagan.", markup(kb))


@router.callback_query(RoleFilter("manager"), F.data.startswith("whb:"))
async def settle_batch(c: CallbackQuery):
    batch = int(c.data.split(":")[1])
    rows = await db_all(
        "SELECT p.id, p.amount AS total, p.day, w.name FROM payouts p JOIN workers w ON w.id=p.worker_id "
        "WHERE p.batch=? ORDER BY w.name",
        (batch,),
    )
    if not rows:
        return await c.answer("Topilmadi", show_alert=True)
    text = f"📜 {rows[0]['day']} hisob-kitobi\n\n" + "\n".join(report_lines(rows))
    text += f"\n\nJami: {money(sum(r['total'] for r in rows))} so'm"
    kb = grid([btn(r["name"], f"whp:{r['id']}") for r in rows])
    kb.append([btn("⬅️ Orqaga", "whist")])
    await edit(c, text, markup(kb))


@router.callback_query(RoleFilter("manager"), F.data.startswith("whp:"))
async def settle_payout(c: CallbackQuery):
    p = await db_one("SELECT * FROM payouts WHERE id=?", (int(c.data.split(":")[1]),))
    if not p:
        return await c.answer("Topilmadi", show_alert=True)
    text = await breakdown_by(p["worker_id"], "payout_id=?", (p["id"],), f"📜 {p['day']} hisob-kitobi")
    await edit(c, text, markup([[btn("⬅️ Orqaga", f"whb:{p['batch']}")]]))


# --- ishchilar ro'yxati
@router.callback_query(RoleFilter("manager"), F.data == "wlist")
async def workers_list(c: CallbackQuery):
    rows = await db_all("SELECT id, name FROM workers WHERE active=1 ORDER BY name")
    kb = grid([btn(r["name"], f"wdet:{r['id']}") for r in rows])
    kb.append([btn("⬅️ Orqaga", "wmenu")])
    await edit(c, "👥 Ishchilar:" if rows else "Hozircha ishchilar yo'q.", markup(kb))


async def worker_detail(wid: int):
    w = await db_one("SELECT id, name, pin FROM workers WHERE id=?", (wid,))
    if not w:
        return "Ishchi topilmadi.", markup([[btn("⬅️ Orqaga", "wlist")]])
    total = await unpaid_total(wid)
    text = (
        f"👷 {w['name']}\n🔑 Kirish kodi: {w['pin']}\n"
        f"💰 Joriy hisob: {money(total)} so'm"
    )
    kb = [
        [btn("📊 Joriy hisob tafsiloti", f"wcb:{wid}"), btn("📅 Oylar", f"wbrk:{wid}:{cur_month()}")],
        [btn("🔄 Yangi kod", f"wcode:{wid}"), btn("🗑 O'chirish", f"wdel:{wid}")],
        [btn("⬅️ Orqaga", "wlist")],
    ]
    return text, markup(kb)


@router.callback_query(RoleFilter("manager"), F.data.startswith("wdet:"))
async def worker_detail_cb(c: CallbackQuery):
    text, kb = await worker_detail(int(c.data.split(":")[1]))
    await edit(c, text, kb)


@router.callback_query(RoleFilter("manager"), F.data.startswith("wcode:"))
async def worker_new_code(c: CallbackQuery):
    wid = int(c.data.split(":")[1])
    await db_exec("UPDATE workers SET pin=? WHERE id=?", (await new_pin(), wid))
    await db_exec("DELETE FROM users WHERE worker_id=?", (wid,))  # eski kod bilan kirganlar chiqarib yuboriladi
    text, kb = await worker_detail(wid)
    await edit(c, "✅ Yangi kod berildi.\n\n" + text, kb)


@router.callback_query(RoleFilter("manager"), F.data.startswith("wdel:"))
async def worker_delete_ask(c: CallbackQuery):
    wid = c.data.split(":")[1]
    kb = [[btn("✅ Ha, o'chirish", f"wdelok:{wid}"), btn("❌ Yo'q", f"wdet:{wid}")]]
    await edit(c, "Ishchini o'chirasizmi? (Oldingi oylik hisobi saqlanib qoladi.)", markup(kb))


@router.callback_query(RoleFilter("manager"), F.data.startswith("wdelok:"))
async def worker_delete_ok(c: CallbackQuery):
    wid = int(c.data.split(":")[1])
    await db_exec("UPDATE workers SET active=0 WHERE id=?", (wid,))
    await db_exec("DELETE FROM users WHERE worker_id=?", (wid,))
    await edit(c, "🗑 Ishchi o'chirildi.", markup([[btn("⬅️ Orqaga", "wlist")]]))


# --- ishchi qo'shish
@router.callback_query(RoleFilter("manager"), F.data == "waddw")
async def add_worker(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddWorker.name)
    await c.message.answer("Ishchining ismini yozing:")
    await c.answer()


@router.message(AddWorker.name, F.text)
async def add_worker_name(m: Message, state: FSMContext):
    name = m.text.strip()
    pin = await new_pin()
    await db_exec("INSERT INTO workers(name, pin) VALUES(?,?)", (name, pin))
    await state.clear()
    await m.answer(
        f"✅ {name} qo'shildi.\n🔑 Kirish kodi: {pin}\n\n"
        "Shu kodni ishchiga bering: botda «👷 Ishchi» tugmasini bosib shu kodni kiritadi.",
        reply_markup=workers_menu_kb(),
    )


# --- ish turlari va narxlari
KIND_ICON = {"cut": "✂️", "make": "🪡", "simple": "🔢"}
KIND_LABEL = {
    "cut": "✂️ Material ketadi (masalan: bichish)",
    "make": "🪡 Tayyor mahsulot chiqadi (masalan: tikish)",
    "simple": "🔢 Oddiy (faqat soni)",
}
KIND_ORDER = ["cut", "make", "simple"]


async def types_view():
    rows = await db_all("SELECT id, name, price, kind FROM work_types ORDER BY name")
    kb = [
        [btn(f"{KIND_ICON.get(r['kind'] or 'cut', '✂️')} {r['name']} — {money(r['price'])} so'm", f"wtd:{r['id']}")]
        for r in rows
    ]
    kb.append([btn("➕ Ish turi qo'shish", "wtadd")])
    kb.append([btn("⬅️ Orqaga", "wmenu")])
    text = "🛠 Ish turlari (narx 1 dona uchun):" if rows else "Hozircha ish turlari yo'q. Masalan: Bichish, Tikish."
    return text, markup(kb)


@router.callback_query(RoleFilter("manager"), F.data == "wtypes")
async def work_types(c: CallbackQuery):
    text, kb = await types_view()
    await edit(c, text, kb)


@router.callback_query(RoleFilter("manager"), F.data == "wtadd")
async def work_type_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddWorkType.name)
    await c.message.answer("Ish turining nomini yozing (masalan: Tikish):")
    await c.answer()


@router.message(AddWorkType.name, F.text)
async def work_type_name(m: Message, state: FSMContext):
    name = m.text.strip()
    if await db_one("SELECT id FROM work_types WHERE name=?", (name,)):
        await m.answer("Bunday ish turi bor. Boshqa nom yozing:")
        return
    await state.update_data(name=name)
    await state.set_state(AddWorkType.price)
    await m.answer(f"«{name}» — 1 donasi uchun necha so'm to'lanadi? (masalan: 5000)")


@router.message(AddWorkType.price, F.text)
async def work_type_price(m: Message, state: FSMContext):
    price = parse_money(m.text)
    if price is None:
        await m.answer("Narxni raqam bilan yozing, masalan: 5000")
        return
    await state.update_data(price=price)
    await state.set_state(AddWorkType.kind)
    kb = [[btn(KIND_LABEL[k], f"wtkind:{k}")] for k in KIND_ORDER]
    await m.answer(
        "Bu ish qanday?\n\n"
        "• Material ketadi: ishchi materialni tanlab, necha metr ketganini yozadi (ombordan ayriladi).\n"
        "• Tayyor mahsulot chiqadi: ishchi nima tikkanini yozadi (postel, necha kishilik, nomi, rasmi) va u "
        "yangi bo'limga tushadi, narxini siz qo'yasiz.\n"
        "• Oddiy: faqat nechta qilgani yoziladi.",
        reply_markup=markup(kb),
    )


@router.callback_query(RoleFilter("manager"), AddWorkType.kind, F.data.startswith("wtkind:"))
async def work_type_kind(c: CallbackQuery, state: FSMContext):
    kind = c.data.split(":")[1]
    if kind not in KIND_LABEL:
        return await c.answer()
    d = await state.get_data()
    await db_exec("INSERT INTO work_types(name, price, kind) VALUES(?,?,?)", (d["name"], d["price"], kind))
    await state.clear()
    text, kb = await types_view()
    await edit(c, f"✅ «{d['name']}» — {money(d['price'])} so'm ({KIND_LABEL[kind]}).\n\n{text}", kb)


@router.message(AddWorkType.kind)
async def work_type_kind_hint(m: Message):
    await m.answer("Tugmalardan birini tanlang.")


async def work_type_card(tid: int):
    t = await db_one("SELECT id, name, price, kind FROM work_types WHERE id=?", (tid,))
    if not t:
        return None, None
    kb = [
        [btn("💲 Narxni o'zgartirish", f"wtprice:{tid}"), btn("🔁 Turini almashtirish", f"wtkc:{tid}")],
        [btn("🗑 O'chirish", f"wtdel:{tid}")],
        [btn("⬅️ Orqaga", "wtypes")],
    ]
    text = f"🛠 {t['name']}\n💰 1 dona: {money(t['price'])} so'm\n📌 Turi: {KIND_LABEL.get(t['kind'] or 'cut')}"
    return text, markup(kb)


@router.callback_query(RoleFilter("manager"), F.data.startswith("wtd:"))
async def work_type_detail(c: CallbackQuery):
    text, kb = await work_type_card(int(c.data.split(":")[1]))
    if not text:
        return await c.answer("Topilmadi", show_alert=True)
    await edit(c, text, kb)


@router.callback_query(RoleFilter("manager"), F.data.startswith("wtkc:"))
async def work_type_kind_cycle(c: CallbackQuery):
    tid = int(c.data.split(":")[1])
    t = await db_one("SELECT kind FROM work_types WHERE id=?", (tid,))
    if not t:
        return await c.answer("Topilmadi", show_alert=True)
    cur = t["kind"] if t["kind"] in KIND_ORDER else "cut"
    new = KIND_ORDER[(KIND_ORDER.index(cur) + 1) % len(KIND_ORDER)]
    await db_exec("UPDATE work_types SET kind=? WHERE id=?", (new, tid))
    text, kb = await work_type_card(tid)
    await edit(c, text, kb)


@router.callback_query(RoleFilter("manager"), F.data == "wpend")
async def pending_products(c: CallbackQuery):
    rows = await db_all(
        "SELECT p.id, p.name, p.quantity, p.unit, cat.name AS cname FROM products p "
        "JOIN categories cat ON cat.id=p.category_id WHERE COALESCE(p.pending,0)=1 ORDER BY p.id DESC"
    )
    kb = [[btn(f"{r['cname']}: {r['name']} — {fmt(r['quantity'])} {r['unit']}", f"prod:v:{r['id']}")] for r in rows]
    kb.append([btn("⬅️ Orqaga", "wmenu")])
    text = (
        "🆕 Narxi qo'yilmagan tayyor mahsulotlar.\nBittasini bossangiz, «Narxni o'zgartirish» tugmasi chiqadi:"
        if rows else "✅ Narx kutayotgan mahsulot yo'q."
    )
    await edit(c, text, markup(kb))


@router.callback_query(RoleFilter("manager"), F.data.startswith("wtprice:"))
async def work_type_set_price(c: CallbackQuery, state: FSMContext):
    await state.set_state(SetWorkPrice.price)
    await state.update_data(tid=int(c.data.split(":")[1]))
    await c.message.answer(
        "Yangi narxni yozing (so'mda).\nEslatma: yangi narx faqat bundan keyin kiritiladigan ishlarga tegishli, "
        "oldingilari eski narx bilan qoladi."
    )
    await c.answer()


@router.message(SetWorkPrice.price, F.text)
async def work_type_price_val(m: Message, state: FSMContext):
    price = parse_money(m.text)
    if price is None:
        await m.answer("Narxni raqam bilan yozing, masalan: 5000")
        return
    tid = (await state.get_data())["tid"]
    await db_exec("UPDATE work_types SET price=? WHERE id=?", (price, tid))
    await state.clear()
    t = await db_one("SELECT name, price FROM work_types WHERE id=?", (tid,))
    await m.answer(f"✅ {t['name']}: {money(t['price'])} so'm")


@router.callback_query(RoleFilter("manager"), F.data.startswith("wtdel:"))
async def work_type_del_ask(c: CallbackQuery):
    tid = c.data.split(":")[1]
    kb = [[btn("✅ Ha, o'chirish", f"wtdelok:{tid}"), btn("❌ Yo'q", f"wtd:{tid}")]]
    await edit(c, "Ish turini o'chirasizmi? (Oldin kiritilgan ishlar hisobda qoladi.)", markup(kb))


@router.callback_query(RoleFilter("manager"), F.data.startswith("wtdelok:"))
async def work_type_del_ok(c: CallbackQuery):
    await db_exec("DELETE FROM work_types WHERE id=?", (int(c.data.split(":")[1]),))
    text, kb = await types_view()
    await edit(c, "🗑 O'chirildi.\n\n" + text, kb)


# ---------------------------------------------------------------- MENEJER
@router.message(RoleFilter("manager"), F.text == BTN_TODAY)
async def today(m: Message, state: FSMContext):
    await state.clear()
    kb = [
        [btn("💰 Qarzi borlar", "m:debts")],
        [btn("📦 Do'kondagi narsalar", "cats:v")],
        [btn("🧾 Bugungi sotuvlar", "m:sales")],
    ]
    await m.answer("📊 Do'kon haqida bugungi ma'lumot:", reply_markup=markup(kb))


@router.callback_query(RoleFilter("manager"), F.data == "m:debts")
async def m_debts(c: CallbackQuery):
    await edit(c, await debts_text(), markup([[btn("⬅️ Orqaga", "m:back")]]))


@router.callback_query(RoleFilter("manager"), F.data == "m:sales")
async def m_sales(c: CallbackQuery):
    await edit(c, await sales_text(), markup([[btn("⬅️ Orqaga", "m:back")]]))


@router.callback_query(RoleFilter("manager"), F.data == "m:back")
async def m_back(c: CallbackQuery):
    kb = [
        [btn("💰 Qarzi borlar", "m:debts")],
        [btn("📦 Do'kondagi narsalar", "cats:v")],
        [btn("🧾 Bugungi sotuvlar", "m:sales")],
    ]
    await edit(c, "📊 Do'kon haqida bugungi ma'lumot:", markup(kb))


# --- bo'lim qo'shish
@router.callback_query(RoleFilter("manager"), F.data == "addcat")
async def addcat(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddCat.name)
    await c.message.answer("Yangi bo'lim nomini yozing (masalan: Postel material):")
    await c.answer()


@router.message(AddCat.name, F.text)
async def addcat_name(m: Message, state: FSMContext):
    name = m.text.strip()
    if await db_one("SELECT id FROM categories WHERE name=?", (name,)):
        await m.answer("Bunday bo'lim allaqachon bor. Boshqa nom yozing:")
        return
    await state.update_data(name=name)
    await state.set_state(AddCat.unit)
    kb = [[btn("📏 Metr (material, mato)", "catunit:metr"), btn("🔢 Dona (postel)", "catunit:dona")]]
    await m.answer("Bu bo'limdagi narsalar nima bilan hisoblanadi?", reply_markup=markup(kb))


@router.callback_query(RoleFilter("manager"), AddCat.unit, F.data.startswith("catunit:"))
async def addcat_unit(c: CallbackQuery, state: FSMContext):
    unit = c.data.split(":")[1]
    name = (await state.get_data())["name"]
    await db_exec("INSERT INTO categories(name, unit) VALUES(?,?)", (name, unit))
    await state.clear()
    text, kb = await cats_view("v", "manager")
    await edit(c, f"✅ «{name}» bo'limi qo'shildi (hisob: {unit}).\n\n{text}", kb)


@router.callback_query(RoleFilter("manager"), F.data.startswith("unit:"))
async def toggle_unit(c: CallbackQuery):
    cid = int(c.data.split(":")[1])
    cat = await db_one("SELECT unit FROM categories WHERE id=?", (cid,))
    if not cat:
        return await c.answer("Topilmadi", show_alert=True)
    new_unit = "dona" if cat["unit"] == "metr" else "metr"
    await db_exec("UPDATE categories SET unit=? WHERE id=?", (new_unit, cid))
    await db_exec("UPDATE products SET unit=? WHERE category_id=?", (new_unit, cid))
    text, kb = await prods_view("v", cid, "manager")
    await edit(c, f"✅ Birlik: {new_unit}\n\n{text}", kb)


# --- mahsulot qo'shish
@router.callback_query(RoleFilter("manager"), F.data.startswith("addprod:"))
async def addprod(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddProd.name)
    await state.update_data(cid=int(c.data.split(":")[1]))
    await c.message.answer("Mahsulot nomini yozing (masalan: Xavi siydam):")
    await c.answer()


@router.message(AddProd.name, F.text)
async def addprod_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(AddProd.qty)
    cid = (await state.get_data())["cid"]
    cat = await db_one("SELECT unit FROM categories WHERE id=?", (cid,))
    await m.answer(f"Qancha bor? Faqat son yozing ({cat['unit']} hisobida), masalan: 20")


@router.message(AddProd.qty, F.text)
async def addprod_qty(m: Message, state: FSMContext):
    qty, _ = parse_qty(m.text)
    if qty is None:
        await m.answer("Faqat son yozing, masalan: 20")
        return
    await state.update_data(qty=qty)
    cid = (await state.get_data())["cid"]
    cat = await db_one("SELECT unit FROM categories WHERE id=?", (cid,))
    await state.set_state(AddProd.price)
    await m.answer(f"💰 Narxi qancha? 1 {cat['unit']} uchun so'mda yozing, masalan: 45000")


@router.message(AddProd.price, F.text)
async def addprod_price(m: Message, state: FSMContext):
    price = parse_money(m.text)
    if price is None:
        await m.answer("Narxni raqam bilan yozing, masalan: 45000")
        return
    await state.update_data(price=price)
    await state.set_state(AddProd.photo)
    kb = [[btn("⏭ Rasmsiz davom etish", "skipphoto")]]
    await m.answer("📷 Mahsulot rasmini yuboring (yoki tugmani bosib o'tkazib yuboring):", reply_markup=markup(kb))


async def finish_addprod(msg: Message, state: FSMContext, photo):
    d = await state.get_data()
    cat = await db_one("SELECT unit FROM categories WHERE id=?", (d["cid"],))
    await db_exec(
        "INSERT INTO products(category_id, name, quantity, unit, price, photo) VALUES(?,?,?,?,?,?)",
        (d["cid"], d["name"], d["qty"], cat["unit"], d["price"], photo),
    )
    await state.clear()
    text, kb = await prods_view("v", d["cid"], "manager")
    await msg.answer(f"✅ «{d['name']}» qo'shildi.\n\n{text}", reply_markup=kb)


@router.message(AddProd.photo, F.photo)
async def addprod_photo(m: Message, state: FSMContext):
    await finish_addprod(m, state, m.photo[-1].file_id)


@router.callback_query(AddProd.photo, F.data == "skipphoto")
async def addprod_skip(c: CallbackQuery, state: FSMContext):
    await finish_addprod(c.message, state, None)
    await c.answer()


@router.message(AddProd.photo)
async def addprod_photo_hint(m: Message):
    await m.answer("Rasm yuboring (siqilgan rasm sifatida) yoki «Rasmsiz davom etish» tugmasini bosing.")


# --- miqdorni o'zgartirish
@router.callback_query(RoleFilter("manager"), F.data.startswith("setqty:"))
async def setqty(c: CallbackQuery, state: FSMContext):
    await state.set_state(SetQty.qty)
    await state.update_data(pid=int(c.data.split(":")[1]))
    await c.message.answer("Yangi miqdorni yozing (faqat son, masalan: 85):")
    await c.answer()


@router.message(SetQty.qty, F.text)
async def setqty_val(m: Message, state: FSMContext):
    qty, _ = parse_qty(m.text)
    if qty is None:
        await m.answer("Faqat son yozing, masalan: 85")
        return
    pid = (await state.get_data())["pid"]
    await db_exec("UPDATE products SET quantity=? WHERE id=?", (qty, pid))
    await state.clear()
    p = await db_one("SELECT name, quantity, unit FROM products WHERE id=?", (pid,))
    await m.answer(f"✅ {p['name']}: {fmt(p['quantity'])} {p['unit']}")


# --- narxni o'zgartirish
@router.callback_query(RoleFilter("manager"), F.data.startswith("setprice:"))
async def setprice(c: CallbackQuery, state: FSMContext):
    await state.set_state(SetPrice.price)
    await state.update_data(pid=int(c.data.split(":")[1]))
    await c.message.answer("Yangi narxni yozing (so'mda, masalan: 45000):")
    await c.answer()


@router.message(SetPrice.price, F.text)
async def setprice_val(m: Message, state: FSMContext):
    price = parse_money(m.text)
    if price is None:
        await m.answer("Narxni raqam bilan yozing, masalan: 45000")
        return
    pid = (await state.get_data())["pid"]
    await db_exec("UPDATE products SET price=?, pending=0 WHERE id=?", (price, pid))
    await state.clear()
    p = await db_one("SELECT name, price, unit FROM products WHERE id=?", (pid,))
    await m.answer(f"✅ {p['name']}: {money(p['price'])} so'm ({p['unit']} uchun)")


# --- rasm qo'yish (menejer va kassir)
@router.callback_query(RoleFilter("manager", "cashier"), F.data.startswith("photo:"))
async def photo_ask(c: CallbackQuery, state: FSMContext):
    await state.set_state(SetPhoto.photo)
    await state.update_data(pid=int(c.data.split(":")[1]))
    await c.message.answer("📷 Mahsulot rasmini yuboring:")
    await c.answer()


@router.message(SetPhoto.photo, F.photo)
async def photo_save(m: Message, state: FSMContext):
    pid = (await state.get_data())["pid"]
    await db_exec("UPDATE products SET photo=? WHERE id=?", (m.photo[-1].file_id, pid))
    await state.clear()
    await m.answer("✅ Rasm saqlandi.")


@router.message(SetPhoto.photo)
async def photo_hint(m: Message):
    await m.answer("Iltimos, rasm yuboring (siqilgan rasm sifatida).")


# --- o'chirish (tasdiqlash bilan)
@router.callback_query(RoleFilter("manager"), F.data.startswith("del:"))
async def del_ask(c: CallbackQuery):
    _, kind, id_ = c.data.split(":")
    what = "bo'limni (ichidagi mahsulotlar bilan)" if kind == "cat" else "mahsulotni"
    kb = [[btn("✅ Ha, o'chirish", f"delok:{kind}:{id_}"), btn("❌ Yo'q", "cats:v")]]
    await edit(c, f"Rostdan ham {what} o'chirasizmi?", markup(kb))


@router.callback_query(RoleFilter("manager"), F.data.startswith("delok:"))
async def del_ok(c: CallbackQuery):
    _, kind, id_ = c.data.split(":")
    if kind == "cat":
        await db_exec("DELETE FROM products WHERE category_id=?", (int(id_),))
        await db_exec("DELETE FROM categories WHERE id=?", (int(id_),))
    else:
        await db_exec("DELETE FROM products WHERE id=?", (int(id_),))
    text, kb = await cats_view("v", "manager")
    await edit(c, "🗑 O'chirildi.\n\n" + text, kb)


# --- parollarni o'zgartirish
@router.message(RoleFilter("manager"), F.text == BTN_PW)
async def pw_menu(m: Message, state: FSMContext):
    await state.clear()
    kb = [[btn("Menejer paroli", "pw:manager")], [btn("Kassir paroli", "pw:cashier")]]
    await m.answer("Qaysi parolni o'zgartirasiz?", reply_markup=markup(kb))


@router.callback_query(RoleFilter("manager"), F.data.startswith("pw:"))
async def pw_pick(c: CallbackQuery, state: FSMContext):
    role = c.data.split(":")[1]
    await state.set_state(ChangePw.new)
    await state.update_data(role=role)
    name = "Menejer" if role == "manager" else "Kassir"
    await c.message.answer(f"{name} uchun yangi parolni yozing (kamida 4 belgi):")
    await c.answer()


@router.message(ChangePw.new, F.text)
async def pw_set(m: Message, state: FSMContext):
    new = m.text.strip()
    try:
        await m.delete()
    except Exception:
        pass
    if len(new) < 4:
        await m.answer("Parol kamida 4 belgidan iborat bo'lsin. Qayta yozing:")
        return
    role = (await state.get_data())["role"]
    await db_exec("UPDATE settings SET value=? WHERE key=?", (new, f"{role}_password"))
    await state.clear()
    await m.answer("✅ Parol o'zgartirildi.")


# ---------------------------------------------------------------- UMUMIY: narsalarni ko'rish
@router.message(RoleFilter("cashier", "client"), F.text == BTN_ITEMS)
async def items_btn(m: Message, state: FSMContext):
    await state.clear()
    role = await get_role(m.from_user.id)
    text, kb = await cats_view("v", role)
    await m.answer(text, reply_markup=kb)


@router.callback_query(RoleFilter("manager", "cashier", "client"), F.data.startswith("cats:"))
async def cb_cats(c: CallbackQuery):
    role = await get_role(c.from_user.id)
    mode = c.data.split(":")[1]
    if mode == "i" and role != "cashier":
        return await c.answer()
    text, kb = await cats_view(mode, role)
    await edit(c, text, kb)


@router.callback_query(RoleFilter("manager", "cashier", "client"), F.data.startswith("cat:"))
async def cb_cat(c: CallbackQuery):
    role = await get_role(c.from_user.id)
    _, mode, cid = c.data.split(":")
    if mode == "i" and role != "cashier":
        return await c.answer()
    text, kb = await prods_view(mode, int(cid), role)
    await edit(c, text, kb)


@router.callback_query(RoleFilter("manager", "cashier", "client"), F.data.startswith("prod:"))
async def cb_prod(c: CallbackQuery, state: FSMContext):
    role = await get_role(c.from_user.id)
    _, mode, pid = c.data.split(":")
    p = await db_one("SELECT * FROM products WHERE id=?", (int(pid),))
    if not p:
        return await c.answer("Topilmadi", show_alert=True)
    if mode == "i":
        if role != "cashier":
            return await c.answer()
        await state.set_state(StockIn.qty)
        await state.update_data(pid=p["id"])
        await c.message.answer(f"«{p['name']}» — hozir {fmt(p['quantity'])} {p['unit']}.\nNecha {p['unit']} qo'shildi? (son yozing)")
        return await c.answer()
    kb = []
    if role == "manager":
        kb.append([btn("✏️ Miqdorni o'zgartirish", f"setqty:{p['id']}"), btn("💲 Narxni o'zgartirish", f"setprice:{p['id']}")])
        kb.append([btn("🖼 Rasm qo'yish", f"photo:{p['id']}"), btn("🗑 O'chirish", f"del:prod:{p['id']}")])
    elif role == "cashier":
        kb.append([btn("🖼 Rasm qo'yish", f"photo:{p['id']}")])
    kb.append([btn("⬅️ Orqaga", f"cat:v:{p['category_id']}")])
    price = f"{money(p['price'])} so'm ({p['unit']} uchun)" if p["price"] else "belgilanmagan"
    text = f"📦 {p['name']}\n💰 Narxi: {price}\n📊 Qoldiq: {fmt(p['quantity'])} {p['unit']}"
    if p["photo"]:
        try:
            await c.message.delete()
        except Exception:
            pass
        await c.message.answer_photo(p["photo"], caption=text, reply_markup=markup(kb))
        await c.answer()
    else:
        await edit(c, text, markup(kb))


# ---------------------------------------------------------------- KASSIR
@router.message(RoleFilter("cashier"), F.text == BTN_STOCKIN)
async def stock_in_menu(m: Message, state: FSMContext):
    await state.clear()
    text, kb = await cats_view("i", "cashier")
    await m.answer("📥 Kirim: " + text, reply_markup=kb)


@router.message(StockIn.qty, F.text)
async def stock_in_qty(m: Message, state: FSMContext):
    qty, _ = parse_qty(m.text)
    if qty is None:
        await m.answer("Faqat son yozing, masalan: 40")
        return
    pid = (await state.get_data())["pid"]
    await db_exec("UPDATE products SET quantity=quantity+? WHERE id=?", (qty, pid))
    await state.clear()
    p = await db_one("SELECT name, quantity, unit FROM products WHERE id=?", (pid,))
    await m.answer(f"✅ {p['name']}: endi {fmt(p['quantity'])} {p['unit']}")


@router.message(RoleFilter("cashier", "manager"), F.text == BTN_DEBTS)
async def cashier_debts(m: Message):
    await m.answer(await debts_text())


@router.message(RoleFilter("cashier"), F.text == BTN_ADD_DEBT)
async def add_debt(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AddDebt.name)
    await m.answer("Qarzdorning ismini yozing:")


@router.message(AddDebt.name, F.text)
async def add_debt_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(AddDebt.phone)
    await m.answer("Telefon raqamini yozing (masalan: +998991234567):")


@router.message(AddDebt.phone, F.text)
async def add_debt_phone(m: Message, state: FSMContext):
    phone = norm_phone(m.text)
    if not phone:
        await m.answer("Raqam noto'g'ri. Masalan: +998991234567")
        return
    await state.update_data(phone=phone)
    await state.set_state(AddDebt.amount)
    await m.answer("Qarz summasini yozing (so'mda):")


@router.message(AddDebt.amount, F.text)
async def add_debt_amount(m: Message, state: FSMContext):
    amount = parse_money(m.text)
    if not amount:
        await m.answer("Summani raqam bilan yozing, masalan: 150000")
        return
    d = await state.get_data()
    old = await db_one("SELECT amount FROM debtors WHERE phone=?", (d["phone"],))
    if old:
        await db_exec("UPDATE debtors SET amount=amount+?, name=? WHERE phone=?", (amount, d["name"], d["phone"]))
    else:
        await db_exec("INSERT INTO debtors(name, phone, amount) VALUES(?,?,?)", (d["name"], d["phone"], amount))
    total = await db_one("SELECT amount FROM debtors WHERE phone=?", (d["phone"],))
    await state.clear()
    await m.answer(f"✅ {d['name']} ({d['phone']}) — jami qarz: {money(total['amount'])} so'm")


@router.message(RoleFilter("cashier"), F.text == BTN_PAY)
async def pay(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Pay.phone)
    await m.answer("Qarz to'lagan odamning telefon raqamini yozing:")


@router.message(Pay.phone, F.text)
async def pay_phone(m: Message, state: FSMContext):
    phone = norm_phone(m.text)
    row = await db_one("SELECT name, amount FROM debtors WHERE phone=?", (phone,)) if phone else None
    if not row:
        await m.answer("Bu raqam bo'yicha qarzdor topilmadi. Qayta yozing yoki menyudan boshqa bo'limni tanlang.")
        return
    await state.update_data(phone=phone)
    await state.set_state(Pay.amount)
    await m.answer(f"{row['name']} — qarzi {money(row['amount'])} so'm.\nNecha so'm to'ladi?")


@router.message(Pay.amount, F.text)
async def pay_amount(m: Message, state: FSMContext):
    amount = parse_money(m.text)
    if not amount:
        await m.answer("Summani raqam bilan yozing.")
        return
    phone = (await state.get_data())["phone"]
    await db_exec("UPDATE debtors SET amount=MAX(amount-?,0) WHERE phone=?", (amount, phone))
    row = await db_one("SELECT name, amount FROM debtors WHERE phone=?", (phone,))
    await state.clear()
    await m.answer(f"✅ {row['name']} — qolgan qarz: {money(row['amount'])} so'm")


# --- AI bilan sotuvni tahlil qilish
async def parse_sale_ai(text: str, products):
    catalog = "\n".join(f"{p['id']}: {p['name']} ({p['cname']}) [{p['unit']}]" for p in products)
    prompt = (
        "Do'kon kassiri sotilgan tovarni o'zbek tilida erkin yozdi (imlo xatolari bo'lishi mumkin).\n"
        "Quyidagi mahsulotlar ro'yxatidan qaysilari qancha sotilganini aniqla.\n\n"
        f"Mahsulotlar (id: nom (bo'lim) [birlik]):\n{catalog}\n\n"
        f"Kassir yozgani: \"{text}\"\n\n"
        "FAQAT JSON massiv qaytar, boshqa hech narsa yozma. Format: "
        '[{"product_id": 1, "quantity": 3}]. '
        "Agar mahsulotni aniq topa olmasang yoki sotuv haqida gap bo'lmasa, [] qaytar."
    )
    resp = await ai.messages.create(
        model=AI_MODEL, max_tokens=500, messages=[{"role": "user", "content": prompt}]
    )
    raw = "".join(b.text for b in resp.content if b.type == "text")
    found = re.search(r"\[.*\]", raw, re.S)
    return json.loads(found.group(0)) if found else []


# --- BEPUL variant: AI kalitisiz, oddiy matn tahlili
STOP_WORDS = {
    "sotdik", "sotildi", "sotdim", "sotib", "berdik", "berildi", "ketdi", "metr", "metra",
    "m", "dona", "ta", "kg", "kilo", "litr", "va", "yana", "ham", "dan", "ni", "ga",
}


def _norm(s: str) -> str:
    s = s.lower()
    for ch in "ʻ'`’ʼ":
        s = s.replace(ch, "")
    return re.sub(r"[^\w\s]", " ", s)


def _stem(w: str) -> str:
    for suf in ("dan", "ning", "ni", "ga", "lar"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


UNIT_AFTER = re.compile(r"\s*(metra|metr|m|dona|ta|kg|kilo|litr)\b", re.I)


def parse_sale_simple(text: str, products):
    """'xavi siydamdan 3 metr, qizil postel 2 sotildi' kabi matnni tahlil qiladi."""
    results = []
    for seg in re.split(r"[,;\n+]| va ", text, flags=re.I):
        nums = list(re.finditer(r"\d+(?:[.,]\d+)?", seg))
        if not nums:
            continue
        words = [_stem(w) for w in _norm(seg).split()]
        words = [w for w in words if w not in STOP_WORDS and not w.isdigit()]
        query = " ".join(words)
        if not query:
            continue
        best, best_score = None, 0.0
        for p in products:
            name_words = [_stem(w) for w in _norm(p["name"]).split() if not w.isdigit()]
            if not name_words:
                continue
            name = " ".join(name_words)
            score = difflib.SequenceMatcher(None, query, name).ratio()
            if all(w in words for w in name_words):
                score = max(score, 0.95)
            elif query in name or name in query:
                score = max(score, 0.85)
            if score > best_score:
                best, best_score = p, score
        if not best or best_score < 0.6:
            continue
        # mahsulot nomidagi raqamni (masalan "Xavi 2") miqdor deb olmaslik
        name_nums = [w for w in _norm(best["name"]).split() if w.isdigit()]
        cand = []
        for n in nums:
            if n.group(0) in name_nums:
                name_nums.remove(n.group(0))
                continue
            cand.append(n)
        if not cand:
            continue
        # birlik (metr/dona/ta) yonidagi son ustun, bo'lmasa oxirgi son
        pick = next((n for n in cand if UNIT_AFTER.match(seg, n.end())), cand[-1])
        results.append({"product_id": best["id"], "quantity": float(pick.group(0).replace(",", "."))})
    return results


async def parse_sale(text: str, products):
    # AI kaliti bo'lsa AI ishlatiladi, bo'lmasa (yoki xato bo'lsa) bepul tahlil
    if ai is not None:
        try:
            return await parse_sale_ai(text, products)
        except Exception:
            logging.exception("AI ishlamadi, oddiy tahlilga o'tildi")
    return parse_sale_simple(text, products)


@router.message(RoleFilter("cashier"), StateFilter(None), F.text)
async def sale(m: Message):
    products = await db_all(
        "SELECT p.id, p.name, p.quantity, p.unit, c.name cname "
        "FROM products p JOIN categories c ON c.id=p.category_id"
    )
    if not products:
        await m.answer("Omborda hali mahsulot yo'q. Avval menejer mahsulot qo'shsin.")
        return
    try:
        items = await parse_sale(m.text, products)
    except Exception:
        logging.exception("AI xatosi")
        await m.answer("⚠️ Tahlilda xatolik bo'ldi, qayta yozib ko'ring.")
        return
    by_id = {p["id"]: p for p in products}
    today_s = datetime.now(TZ).strftime("%Y-%m-%d")
    lines = []
    for it in items:
        try:
            p = by_id.get(int(it["product_id"]))
            qty = float(it["quantity"])
        except (KeyError, ValueError, TypeError):
            continue
        if not p or qty <= 0:
            continue
        if qty > p["quantity"]:
            lines.append(f"⚠️ {p['name']}: omborda {fmt(p['quantity'])} {p['unit']} bor, {fmt(qty)} sotib bo'lmaydi.")
            continue
        await db_exec("UPDATE products SET quantity=quantity-? WHERE id=?", (qty, p["id"]))
        await db_exec(
            "INSERT INTO sales(product_id, name, qty, unit, day) VALUES(?,?,?,?,?)",
            (p["id"], p["name"], qty, p["unit"], today_s),
        )
        left = p["quantity"] - qty
        lines.append(f"✅ {p['name']}: −{fmt(qty)} {p['unit']} (qoldi: {fmt(left)} {p['unit']})")
    if not lines:
        await m.answer("Tushunmadim 🤔 Mahsulot nomi va miqdorini aniqroq yozing.\nMasalan: «xavi siydamdan 3 metr sotdik»")
        return
    await m.answer("\n".join(lines))


# ---------------------------------------------------------------- MIJOZ
@router.message(RoleFilter("client"), F.text == BTN_MYDEBT)
async def my_debt(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CheckDebt.phone)
    await m.answer("Telefon raqamingizni kiriting (masalan: +998991234567):")


@router.message(CheckDebt.phone, F.text)
async def my_debt_phone(m: Message, state: FSMContext):
    phone = norm_phone(m.text)
    if not phone:
        await m.answer("Raqam noto'g'ri. Masalan: +998991234567")
        return
    row = await db_one("SELECT name, amount FROM debtors WHERE phone=?", (phone,))
    await state.clear()
    if not row or row["amount"] <= 0:
        await m.answer("✅ Bu raqam bo'yicha qarz topilmadi.")
    else:
        await m.answer(f"👤 Ism: {row['name']}\n💰 Qarzingiz: {money(row['amount'])} so'm")


# ---------------------------------------------------------------- ishga tushirish
async def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN o'rnatilmagan: export BOT_TOKEN='...'")
    logging.basicConfig(level=logging.INFO)
    await init_db()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())