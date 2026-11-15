import asyncio, html, json, os
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, ReplyKeyboardRemove

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
CAT_NAMES = {"liquid": "Жижки", "vape": "Вейпы", "snus": "Снюсы", "disp": "Одноразки", "energy": "Энергетики"}
USERS_FILE = "users.json"

dp = Dispatcher()


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

    for it in raw_items:
        p = products.get(it.get("id"))
        try:
            qty = int(it.get("qty", 0))
        except (TypeError, ValueError):
            continue
        if not p or qty < 1 or qty > 50:
            continue
        st = status_in_area(p, area)
        if st == "out":
            continue
        sub = p["price"] * qty
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

    for chat_id in recipients:
        try:
            await bot.send_message(chat_id, admin_text, parse_mode="HTML")
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
