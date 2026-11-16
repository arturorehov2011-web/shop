import asyncio, base64, html, json, os
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import (Message, ReplyKeyboardRemove, CallbackQuery,
                           InlineKeyboardMarkup, InlineKeyboardButton)

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

# Для автоматического уменьшения количества: токен GitHub с правом Contents: Read and write
# (задаётся в Render -> Environment, в код НЕ вставлять)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "arturorehov2011-web/shop")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_FILE = "products.json"
ORDERS_FILE = "orders.json"

dp = Dispatcher()
order_lock = asyncio.Lock()


def load_orders():
    if os.path.exists(ORDERS_FILE):
        try:
            return json.load(open(ORDERS_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_orders(orders):
    # храним только последние 300 заказов
    if len(orders) > 300:
        for k in list(orders)[:len(orders) - 300]:
            orders.pop(k, None)
    try:
        json.dump(orders, open(ORDERS_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as e:
        print("Не удалось сохранить заказы", e)


def apply_stock(text, need):
    """Уменьшает stock у товаров из need {id: qty} в тексте products.json, не трогая остальное форматирование."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    notes = []
    for i, line in enumerate(lines):
        t = line.strip()
        if not t.startswith('{"id"'):
            continue
        comma = t.endswith(",")
        try:
            p = json.loads(t.rstrip(","))
        except Exception:
            continue
        q = need.get(p.get("id"))
        if not q:
            continue
        old = p.get("stock", 1)
        if old < q:
            notes.append(f"{p.get('name')}: в наличии было {old} шт, в заказе {q}")
        p["stock"] = max(0, old - q)
        lines[i] = json.dumps(p, ensure_ascii=False, separators=(",", ":")) + ("," if comma else "")
    return nl.join(lines), notes


async def github_update_stock(items):
    """Списывает товары в products.json на GitHub. Возвращает (ok, notes_or_error)."""
    if not GITHUB_TOKEN:
        return False, "на Render не задан GITHUB_TOKEN"
    need = {}
    for pid, q in items:
        need[pid] = need.get(pid, 0) + q
    api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "shop-bot",
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            for _ in range(3):
                async with s.get(api, params={"ref": GITHUB_BRANCH}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        return False, f"GitHub ответил {r.status} при чтении файла"
                    meta = await r.json()
                text = base64.b64decode(meta["content"]).decode("utf-8")
                new_text, notes = apply_stock(text, need)
                body = {
                    "message": "stock: списание по подтверждённому заказу",
                    "content": base64.b64encode(new_text.encode("utf-8")).decode("ascii"),
                    "sha": meta["sha"],
                    "branch": GITHUB_BRANCH,
                }
                async with s.put(api, json=body, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status in (200, 201):
                        return True, notes
                    if r.status == 409:
                        continue  # файл изменили одновременно, пробуем ещё раз
                    return False, f"GitHub ответил {r.status} при записи файла"
        return False, "файл на GitHub менялся одновременно, попробуй ещё раз"
    except Exception as e:
        return False, f"ошибка связи с GitHub: {e}"


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

    oid = os.urandom(4).hex()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Оплата подтверждена", callback_data=f"pay:{oid}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"no:{oid}"),
    ]])
    sent = []
    for chat_id in recipients:
        try:
            msg = await bot.send_message(chat_id, admin_text, parse_mode="HTML", reply_markup=kb)
            sent.append([chat_id, msg.message_id])
        except Exception as e:
            print("Не удалось отправить", chat_id, e)

    orders = load_orders()
    orders[oid] = {
        "status": "pending", "buyer_id": u.id, "area": area,
        "items": order_items, "text": admin_text,
        "recipients": list(recipients), "messages": sent,
    }
    save_orders(orders)

    reply = (
        f"✅ <b>Заказ принят!</b>\n"
        f"Район: <b>{area_name}</b>\n\n"
        + "\n".join(lines)
        + f"\n\n<b>Итого: {total} ₽</b>\n\n"
        + f"📩 Напишите этому человеку по договору о времени и оплате: {worker_tag}"
    )
    if has_preorder:
        reply += "\n\n⏳ В заказе есть товары по предзаказу, сроки уточняйте при переписке."

    await m.answer(reply, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())


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

    await m.answer(
        "Привет! Нажми синюю кнопку «Магазин» слева внизу, выбери товары и оформи заказ." + staff_note,
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.callback_query(F.data.startswith(("pay:", "no:")))
async def on_decision(c: CallbackQuery, bot: Bot):
    action, oid = c.data.split(":", 1)
    async with order_lock:
        orders = load_orders()
        o = orders.get(oid)
        if not o:
            await c.answer("Заказ не найден (бот мог перезапуститься). Поправь stock в products.json вручную.", show_alert=True)
            return
        if c.from_user.id not in o["recipients"]:
            await c.answer("Это не твой заказ.", show_alert=True)
            return
        if o["status"] != "pending":
            await c.answer("Этот заказ уже обработан.", show_alert=True)
            return

        who = f"@{c.from_user.username}" if c.from_user.username else c.from_user.full_name
        notes = []
        if action == "pay":
            ok, res = await github_update_stock([tuple(x) for x in o["items"]])
            if not ok:
                await c.answer(f"Не удалось обновить количество: {res}", show_alert=True)
                return
            notes = res
            o["status"] = "paid"
            label = "✅ Оплата подтверждена, количество обновлено"
        else:
            o["status"] = "cancelled"
            label = "❌ Заказ отменён, количество не менялось"
        orders[oid] = o
        save_orders(orders)

    final = o["text"] + f"\n\n<b>{label}</b> ({html.escape(who)})"
    if notes:
        final += "\n<i>⚠️ " + html.escape("; ".join(notes)) + "</i>"
    for chat_id, msg_id in o["messages"]:
        try:
            await bot.edit_message_text(final, chat_id=chat_id, message_id=msg_id, parse_mode="HTML", reply_markup=None)
        except Exception as e:
            print("Не удалось обновить сообщение", chat_id, e)
    if action == "pay":
        try:
            await bot.send_message(o["buyer_id"], "✅ Оплата подтверждена. Спасибо за заказ!")
        except Exception:
            pass
    await c.answer("Готово")


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