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
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
from api import start_api
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

ROLE_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_MGR), KeyboardButton(text=BTN_CSH), KeyboardButton(text=BTN_CLI)]],
    resize_keyboard=True,
)

ALL_BUTTONS = {
    BTN_MGR, BTN_CSH, BTN_CLI, BTN_EXIT, BTN_TODAY, BTN_PW, BTN_ADD_DEBT, BTN_PAY,
    BTN_STOCKIN, BTN_DEBTS, BTN_ITEMS, BTN_MYDEBT,
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
        rows = [[BTN_TODAY], [BTN_PW], [BTN_EXIT]]
    elif role == "cashier":
        rows = [[BTN_ADD_DEBT, BTN_PAY], [BTN_STOCKIN, BTN_DEBTS], [BTN_ITEMS], [BTN_EXIT]]
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
            CREATE TABLE IF NOT EXISTS sales(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER, name TEXT, qty REAL, unit TEXT, day TEXT);
            """
        )
        for stmt in (
            "ALTER TABLE categories ADD COLUMN unit TEXT DEFAULT 'metr'",
            "ALTER TABLE products ADD COLUMN price INTEGER DEFAULT 0",
            "ALTER TABLE products ADD COLUMN photo TEXT",
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


async def set_role(tg_id: int, role):
    if role is None:
        await db_exec("DELETE FROM users WHERE tg_id=?", (tg_id,))
    else:
        await db_exec("INSERT OR REPLACE INTO users(tg_id, role) VALUES(?,?)", (tg_id, role))


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
    rows = await db_all("SELECT id, name FROM products WHERE category_id=? ORDER BY name", (cid,))
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
    await db_exec("UPDATE products SET price=? WHERE id=?", (price, pid))
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
    await start_api(bot, db_all, db_one, db_exec, get_setting, parse_sale)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())