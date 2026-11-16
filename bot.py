import asyncio, base64, html, json, os
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import (Message, ReplyKeyboardRemove, CallbackQuery, ReplyKeyboardMarkup,
                           KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton)

TOKEN = os.environ.get("BOT_TOKEN", "8729973602:AAHpgqrNx72hBy2lYp-VBxrlH0ekz7KGnK0")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://arturorehov2011-web.github.io/shop").rstrip("/")
RAW_JSON_URL = "https://raw.githubusercontent.com/arturorehov2011-web/shop/main/products.json"
ADMINS = [6981934499]

AREAS = {
    "ilinka": "Ильинка",
    "nekrasovka": "Некрасовка",
    "yuzhny": "Южный",
    "krechka": "Красная речка",
}

AREA_WORKERS = {
    "yuzhny": "@DioVod_973",
    "krechka": "@ugadair",
    "nekrasovka": "@kityshaa",
    "ilinka": "@dahqwll1dirt",
}

BOSSES = ["dahqwll1dirt", "holoun"]
CAT_NAMES = {"liquid": "Жижки", "vape": "Вейпы", "snus": "Снюсы", "disp": "Одноразки", "energy": "Энергетики", "consumable": "Расходники", "condom": "Презики"}
USERS_FILE = "users.json"

# Для автоматического учёта количества: токен GitHub с правом Contents: Read and write
# (задаётся в Render -> Environment, в код НЕ вставлять)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "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").strip().strip("'\"")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "arturorehov2011-web/shop")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_FILE = "products.json"
PAGE_SIZE = 8

dp = Dispatcher()
stock_lock = asyncio.Lock()


def apply_stock(text, deltas, warn=True, absolute=False):
    """Меняет stock у товаров {id: изменение} в тексте products.json, не трогая остальное форматирование.
    Возвращает (новый_текст, предупреждения, {id: товар_после_изменения})."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    notes, changed = [], {}
    for i, line in enumerate(lines):
        t = line.strip()
        if not t.startswith('{"id"'):
            continue
        comma = t.endswith(",")
        try:
            p = json.loads(t.rstrip(","))
        except Exception:
            continue
        d = deltas.get(p.get("id"))
        if d is None or (d == 0 and not absolute):
            continue
        old = p.get("stock", 1)
        if warn and not absolute and old + d < 0:
            notes.append(f"{p.get('name')}: в наличии было {old} шт, в заказе {-d}")
        p["stock"] = max(0, d if absolute else old + d)
        changed[p["id"]] = p
        lines[i] = json.dumps(p, ensure_ascii=False, separators=(",", ":")) + ("," if comma else "")
    return nl.join(lines), notes, changed


def _gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "shop-bot",
    }


def _gh_api():
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"


async def _gh_401(r):
    """Подробное описание ошибки 401: что ответил GitHub и какой токен видит бот (сам токен не показывается)."""
    try:
        gh_msg = (await r.json(content_type=None)).get("message", "")
    except Exception:
        gh_msg = ""
    src = "переменная GITHUB_TOKEN на Render" if os.environ.get("GITHUB_TOKEN") else "значение, вписанное в код"
    return (f"GitHub 401 ({gh_msg}). Токен взят из: {src}. "
            f"Начало {GITHUB_TOKEN[:23]!r}, конец {GITHUB_TOKEN[-4:]!r}, длина {len(GITHUB_TOKEN)}")


async def github_change_stock(deltas, reason, warn=True, absolute=False):
    """Меняет количество в products.json на GitHub.
    Возвращает (True, {"notes": [...], "products": {id: товар}}) или (False, текст ошибки)."""
    if not GITHUB_TOKEN:
        return False, "на Render не задан GITHUB_TOKEN"
    try:
        async with stock_lock:
            async with aiohttp.ClientSession(headers=_gh_headers(), timeout=aiohttp.ClientTimeout(total=15)) as s:
                for _ in range(3):
                    async with s.get(_gh_api(), params={"ref": GITHUB_BRANCH}) as r:
                        if r.status == 401:
                            return False, await _gh_401(r)
                        if r.status != 200:
                            return False, f"GitHub ответил {r.status} при чтении файла (проверь доступ токена к репозиторию shop)"
                        meta = await r.json()
                    text = base64.b64decode(meta["content"]).decode("utf-8")
                    new_text, notes, changed = apply_stock(text, deltas, warn, absolute)
                    if new_text == text:
                        return True, {"notes": notes, "products": changed}
                    body = {
                        "message": f"stock: {reason}"[:200],
                        "content": base64.b64encode(new_text.encode("utf-8")).decode("ascii"),
                        "sha": meta["sha"],
                        "branch": GITHUB_BRANCH,
                    }
                    async with s.put(_gh_api(), json=body) as r:
                        if r.status in (200, 201):
                            return True, {"notes": notes, "products": changed}
                        if r.status == 409:
                            continue  # файл изменили одновременно, пробуем ещё раз
                        if r.status == 401:
                            return False, await _gh_401(r)
                        if r.status in (403, 404):
                            return False, f"GitHub ответил {r.status}: у токена нет права записи (нужно Contents: Read and write и доступ к репозиторию shop)"
                        return False, f"GitHub ответил {r.status} при записи файла"
        return False, "файл на GitHub менялся одновременно, попробуй ещё раз"
    except Exception as e:
        return False, f"ошибка связи с GitHub: {e}"


async def get_catalog():
    """Список товаров: свежий из GitHub (если есть токен), иначе с сайта."""
    if GITHUB_TOKEN:
        try:
            async with aiohttp.ClientSession(headers=_gh_headers(), timeout=aiohttp.ClientTimeout(total=15)) as s:
                async with s.get(_gh_api(), params={"ref": GITHUB_BRANCH}) as r:
                    if r.status == 200:
                        meta = await r.json()
                        return json.loads(base64.b64decode(meta["content"]).decode("utf-8"))
        except Exception as e:
            print("get_catalog:", e)
    products = await load_products()
    return list(products.values())


def load_users():
    if os.path.exists(USERS_FILE):
        try:
            return json.load(open(USERS_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_user(u):
    if not u or not u.username:
        return
    users = load_users()
    uname = u.username.lower().lstrip("@")
    if users.get(uname) != u.id:
        users[uname] = u.id
        try:
            json.dump(users, open(USERS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        except Exception:
            pass


def status_in_area(p, area):
    a = p.get("areas")
    if isinstance(a, dict):
        return a.get(area, "out")
    if isinstance(a, list):
        if p.get("status") == "out" or area not in a:
            return "out"
        return "preorder" if p.get("status") == "preorder" else "in_stock"
    return p.get("status", "in_stock")


async def load_products():
    for url in [f"{RAW_JSON_URL}?t={os.urandom(4).hex()}", f"{WEBAPP_URL}/products.json?t={os.urandom(4).hex()}"]:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=8) as r:
                    if r.status == 200:
                        data = json.loads(await r.text())
                        return {p["id"]: p for p in data}
        except Exception:
            pass
    return {}


async def process_order(m: Message, bot: Bot, area: str, raw_items: list):
    save_user(m.from_user)
    if area not in AREAS:
        area = "ilinka"
    area_name = AREAS[area]
    worker_tag = AREA_WORKERS.get(area, "@dahqwll1dirt")
    worker_uname = worker_tag.lower().lstrip("@")

    products = await load_products()
    lines, total, has_preorder = [], 0, False
    order_items = []

    for it in raw_items:
        p = products.get(it.get("id"))
        try:
            qty = int(it.get("qty", 0))
        except (TypeError, ValueError):
            continue
        if not p or qty < 1:
            continue
        stock = p.get("stock")
        if stock is not None:
            if stock <= 0:
                continue
            qty = min(qty, int(stock))
        st = status_in_area(p, area)
        if st == "out":
            continue
        sub = p["price"] * qty
        order_items.append((p["id"], qty))
        total += sub
        mark = " (ПРЕДЗАКАЗ)" if st == "preorder" else ""
        has_preorder |= (st == "preorder")
        cat = CAT_NAMES.get(p.get("cat"), "")
        lines.append(f"• [{cat}] {html.escape(p['name'])}{mark} × {qty} шт = {sub} ₽")

    if not lines:
        await m.answer("Корзина пустая или выбранных товаров нет в наличии в этом районе.")
        return

    changes = {}
    for pid, q in order_items:
        changes[pid] = changes.get(pid, 0) - q
    stock_ok, stock_res = await github_change_stock(changes, f"заказ, {area_name}")
    stock_warn = ""
    if not stock_ok:
        stock_warn = ("\n\n<i>⚠️ Количество НЕ списалось автоматически: " + html.escape(str(stock_res))
                      + ". Поправь вручную командой /stock.</i>")
    elif stock_res["notes"]:
        stock_warn = "\n\n<i>⚠️ " + html.escape("; ".join(stock_res["notes"])) + "</i>"

    u = m.from_user
    buyer = f"@{u.username}" if u.username else f'<a href="tg://user?id={u.id}">{html.escape(u.full_name)}</a>'

    admin_text = (
        f"<b>📦 Новый заказ ({area_name})</b>\n"
        f"Покупатель: {buyer} (id {u.id})\n"
        f"Отвечает за район: {worker_tag}\n\n"
        + "\n".join(lines)
        + f"\n\n<b>Итого: {total} ₽</b>"
    )

    users = load_users()
    target_unames = {worker_uname} | set(BOSSES)
    recipients = set(ADMINS)
    missing = []

    for uname in target_unames:
        if uname in users:
            recipients.add(users[uname])
        else:
            missing.append(f"@{uname}")

    if missing:
        admin_text += f"\n\n<i>⚠️ Ещё не нажали /start в боте: {', '.join(missing)}</i>"

    boss_ids = set(ADMINS) | {users[x] for x in BOSSES if x in users}
    for chat_id in recipients:
        text = admin_text + (stock_warn if chat_id in boss_ids else "")
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception as e:
            print("Не удалось отправить", chat_id, e)

    reply = (
        f"✅ <b>Заказ принят!</b>\n"
        f"Район: <b>{area_name}</b>\n\n"
        + "\n".join(lines)
        + f"\n\n<b>Итого: {total} ₽</b>\n\n"
        + f"📩 Напишите этому человеку по договору о времени и оплате: {worker_tag}"
    )
    if has_preorder:
        reply += "\n\n⏳ В заказе есть товары по предзаказу, сроки уточняйте при переписке."

    await m.answer(reply, parse_mode="HTML", reply_markup=boss_kb() if is_boss(m.from_user) else ReplyKeyboardRemove())


@dp.message(CommandStart())
async def start(m: Message, command: CommandObject, bot: Bot):
    save_user(m.from_user)
    args = (command.args or "").strip()

    if args.startswith("ord_"):
        try:
            await m.delete()
        except Exception:
            pass
        parts = args.split("_")
        if len(parts) >= 3:
            area = parts[1]
            items = []
            for s in parts[2:]:
                if "-" in s:
                    pid, q = s.rsplit("-", 1)
                    items.append({"id": pid, "qty": int(q)})
            await process_order(m, bot, area, items)
            return

    uname = (m.from_user.username or "").lower()
    all_staff = {w.lstrip("@").lower() for w in AREA_WORKERS.values()} | set(BOSSES)
    staff_note = "\n\n✅ Твой аккаунт подключён к получению заказов!" if uname in all_staff else ""
    if uname in BOSSES or m.from_user.id in ADMINS:
        staff_note += "\n\n📦 Количество товаров меняется кнопкой «📦 Наличие товаров» внизу."

    await m.answer(
        "Привет! Нажми синюю кнопку «Магазин» слева внизу, выбери товары и оформи заказ." + staff_note,
        reply_markup=boss_kb() if is_boss(m.from_user) else ReplyKeyboardRemove(),
    )


STOCK_BTN = "📦 Наличие товаров"


def boss_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=STOCK_BTN)]], resize_keyboard=True, is_persistent=True)


def is_boss(u):
    return (u.username or "").lower() in BOSSES or u.id in ADMINS


def stock_of(p):
    s = p.get("stock")
    return 1 if s is None else int(s)


def stock_cats_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"st:c:{key}:0")] for key, name in CAT_NAMES.items()
    ])


def stock_list_view(items, cat, page):
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 0), pages - 1)
    rows = [
        [InlineKeyboardButton(text=f"[{stock_of(p)}] {p['name'][:40]}", callback_data=f"st:p:{p['id']}:{cat}:{page}")]
        for p in items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"st:c:{cat}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="st:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"st:c:{cat}:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅ Разделы", callback_data="st:m")])
    text = f"<b>{CAT_NAMES.get(cat, cat)}</b>\nВ скобках сколько в наличии. Нажми на товар, чтобы изменить."
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def stock_item_view(p, cat, page):
    n = stock_of(p)
    status = "нет в наличии" if n <= 0 else "в наличии"
    text = f"<b>{html.escape(p['name'])}</b>\nКоличество: <b>{n} шт</b> ({status})\n\nИзменить на сколько-то (−1, +1, +5) или поставить точное число:"
    pid = p["id"]
    def btn(label, mode, val):
        return InlineKeyboardButton(text=label, callback_data=f"st:{mode}:{pid}:{val}:{cat}:{page}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("−1", "a", -1), btn("+1", "a", 1), btn("+5", "a", 5)],
        [btn("0 (нет)", "s", 0), btn("1", "s", 1), btn("2", "s", 2)],
        [btn("3", "s", 3), btn("5", "s", 5), btn("10", "s", 10)],
        [InlineKeyboardButton(text="⬅ К списку", callback_data=f"st:c:{cat}:{page}")],
    ])
    return text, kb


async def safe_edit(c: CallbackQuery, text, kb):
    try:
        await c.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        print("edit:", e)  # например, «message is not modified»


@dp.message(Command("stock"))
@dp.message(F.text == STOCK_BTN)
async def stock_cmd(m: Message):
    save_user(m.from_user)
    if not is_boss(m.from_user):
        return
    await m.answer("📦 <b>Количество товаров</b>\nВыбери раздел:", parse_mode="HTML", reply_markup=stock_cats_kb())


@dp.callback_query(F.data.startswith("st:"))
async def on_stock_cb(c: CallbackQuery):
    if not is_boss(c.from_user):
        await c.answer("Нет доступа", show_alert=True)
        return
    parts = c.data.split(":")
    kind = parts[1]

    if kind == "noop":
        await c.answer()
        return
    if kind == "m":
        await safe_edit(c, "📦 <b>Количество товаров</b>\nВыбери раздел:", stock_cats_kb())
        await c.answer()
        return

    if kind in ("c", "p"):
        catalog = await get_catalog()
        if not catalog:
            await c.answer("Не удалось загрузить товары", show_alert=True)
            return
        if kind == "c":
            cat, page = parts[2], int(parts[3])
            text, kb = stock_list_view([p for p in catalog if p.get("cat") == cat], cat, page)
        else:
            pid, cat, page = parts[2], parts[3], int(parts[4])
            p = next((x for x in catalog if x.get("id") == pid), None)
            if not p:
                await c.answer("Товар не найден", show_alert=True)
                return
            text, kb = stock_item_view(p, cat, page)
        await safe_edit(c, text, kb)
        await c.answer()
        return

    if kind in ("a", "s"):
        pid, d, cat, page = parts[2], parts[3], parts[4], int(parts[5])
        delta = int(d)
        who = f"@{c.from_user.username}" if c.from_user.username else str(c.from_user.id)
        ok, res = await github_change_stock({pid: delta}, f"{pid} {'=' if kind == 's' else ''}{d} от {who}", warn=False, absolute=(kind == "s"))
        if not ok:
            await c.answer(str(res)[:190], show_alert=True)
            return
        p = res["products"].get(pid)
        if not p:
            await c.answer("Товар не найден", show_alert=True)
            return
        text, kb = stock_item_view(p, cat, page)
        await safe_edit(c, text, kb)
        await c.answer("Готово")
        return

    await c.answer()


@dp.message(F.web_app_data)
async def on_order(m: Message, bot: Bot):
    data = json.loads(m.web_app_data.data)
    await process_order(m, bot, data.get("area", "ilinka"), data.get("items", []))


async def handle_ping(request):
    return web.Response(text="Bot is running!")


async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    bot = Bot(TOKEN)
    me = await bot.get_me()
    print(f"=== БОТ @{me.username} УСПЕШНО ЗАПУЩЕН НА RENDER 24/7! ===")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())