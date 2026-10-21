"""
Amani Home ilovasi uchun HTTP API.

bot.py bilan BIR jarayonda ishlaydi va BIR xil shop.db bazasidan foydalanadi,
shuning uchun botda nima o'zgarsa, ilovada ham shu ko'rinadi (va aksincha).
Rasmlar Telegram'dan olinadi (bot orqali), shuning uchun botda qo'yilgan rasmlar ilovada ham chiqadi.
"""
import os
import re
import secrets
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from aiohttp import web

TZ = ZoneInfo("Asia/Tashkent")
TOKENS: dict = {}          # token -> {"role":..., "phone":...}
PHOTO_CACHE: dict = {}     # telegram file_id -> bytes


# ---------------------------------------------------------------- yordamchilar
def err(msg: str, status: int = 400):
    return web.json_response({"error": msg}, status=status)


def ok(message: str = "OK", **extra):
    return web.json_response({"message": message, **extra})


def rows(r):
    return [dict(x) for x in r]


def norm_phone(text):
    d = re.sub(r"\D", "", str(text or ""))
    if len(d) == 9:
        d = "998" + d
    if len(d) != 12 or not d.startswith("998"):
        return None
    return "+" + d


def to_num(v, cast=float, allow_zero=False):
    try:
        x = cast(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if x < 0 or (x == 0 and not allow_zero):
        return None
    return x


def fmt(x) -> str:
    x = float(x)
    return str(int(x)) if x == int(x) else f"{x:.2f}".rstrip("0").rstrip(".")


def money(x) -> str:
    return f"{int(x):,}".replace(",", " ")


async def body(request) -> dict:
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


@web.middleware
async def auth_mw(request, handler):
    if request.path == "/api/login" or request.path.startswith("/api/photo/"):
        return await handler(request)
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    user = TOKENS.get(token)
    if not user:
        return err("Qayta kiring", 401)
    request["user"] = user
    return await handler(request)


def allowed(request, *roles) -> bool:
    return request["user"]["role"] in roles


# ---------------------------------------------------------------- asosiy qism
def build_app(bot, db_all, db_one, db_exec, get_setting, parse_sale):
    r = web.RouteTableDef()

    # ---- kirish
    @r.post("/api/login")
    async def login(request):
        d = await body(request)
        role = d.get("role")
        phone = norm_phone(d.get("phone"))
        if not phone:
            return err("Telefon raqam noto'g'ri. Masalan: +998 90 123 45 67")
        if role in ("manager", "cashier"):
            if str(d.get("password") or "").strip() != await get_setting(f"{role}_password"):
                return err("Parol noto'g'ri", 403)
        elif role != "client":
            return err("Noto'g'ri rol")
        await db_exec("INSERT OR IGNORE INTO app_clients(phone, created) VALUES(?,?)",
                      (phone, datetime.now(TZ).isoformat()))
        token = secrets.token_hex(24)
        TOKENS[token] = {"role": role, "phone": phone}
        return web.json_response({"token": token, "role": role, "phone": phone})

    # ---- hamma uchun: bo'limlar, mahsulotlar, rasm
    @r.get("/api/categories")
    async def categories(request):
        return web.json_response(rows(await db_all("SELECT id, name, unit FROM categories ORDER BY name")))

    @r.get("/api/products")
    async def products(request):
        sql = (
            "SELECT p.id, p.name, p.category_id, c.name AS category, p.quantity, p.unit, "
            "COALESCE(p.price,0) AS price, (p.photo IS NOT NULL) AS has_photo "
            "FROM products p JOIN categories c ON c.id=p.category_id WHERE 1=1"
        )
        params = []
        cid = request.query.get("category_id")
        if cid:
            sql += " AND p.category_id=?"
            params.append(int(cid))
        sql += " ORDER BY p.name"
        data = rows(await db_all(sql, params))
        q = request.query.get("q", "").strip().lower()
        if q:
            data = [x for x in data if q in x["name"].lower()]
        return web.json_response(data)

    @r.get("/api/photo/{pid}")
    async def photo(request):
        p = await db_one("SELECT photo FROM products WHERE id=?", (int(request.match_info["pid"]),))
        if not p or not p["photo"]:
            raise web.HTTPNotFound()
        fid = p["photo"]
        data = PHOTO_CACHE.get(fid)
        if data is None:
            buf = BytesIO()
            await bot.download(fid, destination=buf)
            data = buf.getvalue()
            if len(PHOTO_CACHE) > 300:
                PHOTO_CACHE.clear()
            PHOTO_CACHE[fid] = data
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    # ---- mijoz
    @r.get("/api/my-debt")
    async def my_debt(request):
        row = await db_one("SELECT name, amount FROM debtors WHERE phone=?", (request["user"]["phone"],))
        if not row or row["amount"] <= 0:
            return web.json_response({"name": None, "amount": 0})
        return web.json_response({"name": row["name"], "amount": row["amount"]})

    # ---- kassir / menejer: qarzdorlar
    @r.get("/api/debts")
    async def debts(request):
        if not allowed(request, "cashier", "manager"):
            return err("Ruxsat yo'q", 403)
        items = rows(await db_all("SELECT name, phone, amount FROM debtors WHERE amount>0 ORDER BY amount DESC"))
        return web.json_response({"items": items, "total": sum(i["amount"] for i in items)})

    # ---- kassir
    @r.post("/api/stock-in")
    async def stock_in(request):
        if not allowed(request, "cashier"):
            return err("Ruxsat yo'q", 403)
        d = await body(request)
        qty = to_num(d.get("qty"))
        pid = to_num(d.get("product_id"), int)
        if qty is None or pid is None:
            return err("Miqdorni son bilan kiriting")
        if not await db_one("SELECT id FROM products WHERE id=?", (pid,)):
            return err("Mahsulot topilmadi", 404)
        await db_exec("UPDATE products SET quantity=quantity+? WHERE id=?", (qty, pid))
        p = await db_one("SELECT name, quantity, unit FROM products WHERE id=?", (pid,))
        return ok(f"✅ {p['name']}: endi {fmt(p['quantity'])} {p['unit']}")

    @r.post("/api/debtors")
    async def add_debtor(request):
        if not allowed(request, "cashier"):
            return err("Ruxsat yo'q", 403)
        d = await body(request)
        name = str(d.get("name") or "").strip()
        phone = norm_phone(d.get("phone"))
        amount = to_num(d.get("amount"), int)
        if not name or not phone or amount is None:
            return err("Ism, to'g'ri telefon va summani kiriting")
        if await db_one("SELECT id FROM debtors WHERE phone=?", (phone,)):
            await db_exec("UPDATE debtors SET amount=amount+?, name=? WHERE phone=?", (amount, name, phone))
        else:
            await db_exec("INSERT INTO debtors(name, phone, amount) VALUES(?,?,?)", (name, phone, amount))
        t = await db_one("SELECT amount FROM debtors WHERE phone=?", (phone,))
        return ok(f"✅ {name} — jami qarz: {money(t['amount'])} so'm")

    @r.post("/api/pay")
    async def pay(request):
        if not allowed(request, "cashier"):
            return err("Ruxsat yo'q", 403)
        d = await body(request)
        phone = norm_phone(d.get("phone"))
        amount = to_num(d.get("amount"), int)
        if not phone or amount is None:
            return err("Telefon va summani to'g'ri kiriting")
        if not await db_one("SELECT id FROM debtors WHERE phone=?", (phone,)):
            return err("Bu raqam bo'yicha qarzdor topilmadi", 404)
        await db_exec("UPDATE debtors SET amount=MAX(amount-?,0) WHERE phone=?", (amount, phone))
        row = await db_one("SELECT name, amount FROM debtors WHERE phone=?", (phone,))
        return ok(f"✅ {row['name']} — qolgan qarz: {money(row['amount'])} so'm")

    @r.post("/api/sale")
    async def sale(request):
        if not allowed(request, "cashier"):
            return err("Ruxsat yo'q", 403)
        text = str((await body(request)).get("text") or "").strip()
        if not text:
            return err("Matn yozing")
        prods = await db_all(
            "SELECT p.id, p.name, p.quantity, p.unit, c.name cname "
            "FROM products p JOIN categories c ON c.id=p.category_id"
        )
        if not prods:
            return err("Omborda hali mahsulot yo'q")
        try:
            items = await parse_sale(text, prods)
        except Exception:
            return err("Tahlilda xatolik bo'ldi, qayta yozib ko'ring")
        by_id = {p["id"]: p for p in prods}
        today = datetime.now(TZ).strftime("%Y-%m-%d")
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
            await db_exec("INSERT INTO sales(product_id, name, qty, unit, day) VALUES(?,?,?,?,?)",
                          (p["id"], p["name"], qty, p["unit"], today))
            lines.append(f"✅ {p['name']}: −{fmt(qty)} {p['unit']} (qoldi: {fmt(p['quantity'] - qty)} {p['unit']})")
        if not lines:
            return err("Tushunmadim. Mahsulot nomi va miqdorini aniqroq yozing. Masalan: «xavi siydamdan 3 metr sotdik»")
        return ok("Tayyor", lines=lines)

    # ---- menejer
    @r.get("/api/today")
    async def today(request):
        if not allowed(request, "manager"):
            return err("Ruxsat yo'q", 403)
        day = datetime.now(TZ).strftime("%Y-%m-%d")
        debts_ = rows(await db_all("SELECT name, phone, amount FROM debtors WHERE amount>0 ORDER BY amount DESC"))
        sales_ = rows(await db_all("SELECT name, unit, SUM(qty) q FROM sales WHERE day=? GROUP BY name, unit", (day,)))
        return web.json_response({"debts": debts_, "debts_total": sum(i["amount"] for i in debts_), "sales": sales_})

    @r.post("/api/categories")
    async def add_category(request):
        if not allowed(request, "manager"):
            return err("Ruxsat yo'q", 403)
        d = await body(request)
        name = str(d.get("name") or "").strip()
        unit = d.get("unit")
        if not name or unit not in ("metr", "dona"):
            return err("Nom va birlikni (metr/dona) kiriting")
        if await db_one("SELECT id FROM categories WHERE name=?", (name,)):
            return err("Bunday bo'lim allaqachon bor")
        await db_exec("INSERT INTO categories(name, unit) VALUES(?,?)", (name, unit))
        return ok(f"✅ «{name}» bo'limi qo'shildi")

    @r.delete("/api/categories/{cid}")
    async def del_category(request):
        if not allowed(request, "manager"):
            return err("Ruxsat yo'q", 403)
        cid = int(request.match_info["cid"])
        await db_exec("DELETE FROM products WHERE category_id=?", (cid,))
        await db_exec("DELETE FROM categories WHERE id=?", (cid,))
        return ok("🗑 O'chirildi")

    @r.post("/api/products")
    async def add_product(request):
        if not allowed(request, "manager"):
            return err("Ruxsat yo'q", 403)
        d = await body(request)
        name = str(d.get("name") or "").strip()
        qty = to_num(d.get("quantity"), float, True)
        price = to_num(d.get("price"), int, True)
        cat = await db_one("SELECT unit FROM categories WHERE id=?", (d.get("category_id"),))
        if not name or qty is None or price is None or not cat:
            return err("Bo'lim, nom, miqdor va narxni to'g'ri kiriting")
        await db_exec(
            "INSERT INTO products(category_id, name, quantity, unit, price) VALUES(?,?,?,?,?)",
            (d["category_id"], name, qty, cat["unit"], price),
        )
        return ok(f"✅ «{name}» qo'shildi")

    @r.patch("/api/products/{pid}")
    async def edit_product(request):
        if not allowed(request, "manager"):
            return err("Ruxsat yo'q", 403)
        pid = int(request.match_info["pid"])
        d = await body(request)
        qty = to_num(d.get("quantity"), float, True) if d.get("quantity") is not None else None
        price = to_num(d.get("price"), int, True) if d.get("price") is not None else None
        if qty is None and price is None:
            return err("Yangi miqdor yoki narxni kiriting")
        if qty is not None:
            await db_exec("UPDATE products SET quantity=? WHERE id=?", (qty, pid))
        if price is not None:
            await db_exec("UPDATE products SET price=? WHERE id=?", (price, pid))
        return ok("✅ Saqlandi")

    @r.delete("/api/products/{pid}")
    async def del_product(request):
        if not allowed(request, "manager"):
            return err("Ruxsat yo'q", 403)
        await db_exec("DELETE FROM products WHERE id=?", (int(request.match_info["pid"]),))
        return ok("🗑 O'chirildi")

    @r.post("/api/passwords")
    async def passwords(request):
        if not allowed(request, "manager"):
            return err("Ruxsat yo'q", 403)
        d = await body(request)
        role, new = d.get("role"), str(d.get("password") or "").strip()
        if role not in ("manager", "cashier"):
            return err("Noto'g'ri rol")
        if len(new) < 4:
            return err("Parol kamida 4 belgidan iborat bo'lsin")
        await db_exec("UPDATE settings SET value=? WHERE key=?", (new, f"{role}_password"))
        return ok("✅ Parol o'zgartirildi")

    app = web.Application(middlewares=[auth_mw])
    app.add_routes(r)
    return app


async def start_api(bot, db_all, db_one, db_exec, get_setting, parse_sale, port: int = 8080):
    await db_exec("CREATE TABLE IF NOT EXISTS app_clients(phone TEXT PRIMARY KEY, created TEXT)")
    app = build_app(bot, db_all, db_one, db_exec, get_setting, parse_sale)
    port = int(os.getenv("PORT", port))  # Render/Railway kabi serverlar PORT beradi
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    return runner
