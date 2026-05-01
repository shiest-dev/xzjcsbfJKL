import asyncio
import aiohttp
import logging
import os
import re
import tempfile
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = “8696209920:AAGPiTo98N2b10UUsxnNxoNRP9Ex9XEZP5Y”

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(**name**)

GETGEMS_GQL = “https://api.getgems.io/graphql”
USERNAMES_COLLECTION = “EQCA14o1-VWhS2efqoh_9M1b_A9DtKTuoqfmkn83AbJzwnPi”

HEADERS = {
“Content-Type”: “application/json”,
“Accept”: “application/json”,
“Origin”: “https://getgems.io”,
“Referer”: “https://getgems.io/”,
“User-Agent”: “Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36”,
}

NFT_QUERY_DIRECT = “””
query NftSearchDirect($collectionAddress: String!, $first: Int!, $after: String) {
alphaNftItemSearch(
collections: [$collectionAddress]
first: $first
after: $after
orderBy: { order: ASC, field: PRICE }
filter: { saleType: [fix_price] }
) {
cursor
items {
name
sale {
… on NftSaleFixPrice {
fullPrice
}
}
}
}
}
“””

NFT_QUERY_AUCTION = “””
query NftSearchAuction($collectionAddress: String!, $first: Int!, $after: String) {
alphaNftItemSearch(
collections: [$collectionAddress]
first: $first
after: $after
orderBy: { order: ASC, field: PRICE }
filter: { saleType: [auction] }
) {
cursor
items {
name
sale {
… on NftSaleAuction {
minBid
maxBid
}
}
}
}
}
“””

def load_wordlist(path):
words = set()
try:
with open(path, “r”, encoding=“utf-8”, errors=“ignore”) as f:
for line in f:
w = line.strip().lower()
if w and w.isalpha():
words.add(w)
except FileNotFoundError:
pass
return words

ENGLISH_WORDS = load_wordlist(”/usr/share/dict/words”)
if not ENGLISH_WORDS:
ENGLISH_WORDS = load_wordlist(”/usr/share/dict/american-english”)
if not ENGLISH_WORDS:
ENGLISH_WORDS = load_wordlist(”/usr/share/dict/british-english”)

SPANISH_WORDS = load_wordlist(”/usr/share/dict/spanish”)
if not SPANISH_WORDS:
try:
import subprocess
result = subprocess.run([“find”, “/usr/share/dict”, “-name”, “*spanish*”], capture_output=True, text=True)
for p in result.stdout.strip().split(”\n”):
if p:
SPANISH_WORDS = load_wordlist(p)
if SPANISH_WORDS:
break
except Exception:
pass

OG_MAX_LENGTH = 4

log.info(“Loaded %d english words, %d spanish words”, len(ENGLISH_WORDS), len(SPANISH_WORDS))

def nanos_to_ton(nanos_str):
try:
nanos = int(nanos_str)
ton = nanos / 1_000_000_000
if ton == int(ton):
return int(ton)
return round(ton, 4)
except Exception:
return None

def is_og(username):
return len(username) <= OG_MAX_LENGTH and username.isalpha()

def classify_username(username, filters):
username_lower = username.lower()
if not username_lower.isalpha():
return False
if “english” in filters and username_lower in ENGLISH_WORDS:
return True
if “spanish” in filters and username_lower in SPANISH_WORDS:
return True
if “og” in filters and is_og(username_lower):
return True
return False

async def scrape_sale_type(session, max_ton, filters, query, sale_label, seen, page_offset=0, progress_cb=None):
results = []
cursor = None
page = page_offset

```
while True:
    variables = {
        "collectionAddress": USERNAMES_COLLECTION,
        "first": 100,
    }
    if cursor:
        variables["after"] = cursor

    try:
        async with session.post(
            GETGEMS_GQL,
            json={"query": query, "variables": variables},
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                log.warning("HTTP %d on page %d", resp.status, page)
                break
            data = await resp.json()
    except Exception as e:
        log.error("Request failed page %d: %s", page, e)
        break

    search_data = data.get("data", {}).get("alphaNftItemSearch", {})
    items = search_data.get("items", [])
    new_cursor = search_data.get("cursor")

    if not items:
        break

    price_exceeded = False

    for item in items:
        name = item.get("name", "")
        sale = item.get("sale") or {}

        if sale_label == "direct":
            raw_price = sale.get("fullPrice")
        else:
            raw_price = sale.get("minBid") or sale.get("maxBid")

        if not raw_price:
            continue

        ton_price = nanos_to_ton(raw_price)
        if ton_price is None:
            continue

        if max_ton is not None and ton_price > max_ton:
            price_exceeded = True
            continue

        username = re.sub(r"[^a-zA-Z]", "", name).lower()
        if not username or username in seen:
            continue

        if classify_username(username, filters):
            seen.add(username)
            results.append((username, ton_price, sale_label))

    page += 1
    if progress_cb and page % 5 == 0:
        await progress_cb(page, len(results))

    if not new_cursor or new_cursor == cursor:
        break

    if price_exceeded and max_ton is not None:
        break

    cursor = new_cursor
    await asyncio.sleep(0.3)

return results
```

async def scrape_getgems(session, max_ton, filters, sale_mode, progress_cb=None):
seen = set()
all_results = []

```
if sale_mode in ("direct", "both"):
    direct = await scrape_sale_type(
        session, max_ton, filters,
        NFT_QUERY_DIRECT, "direct", seen,
        page_offset=0, progress_cb=progress_cb
    )
    all_results.extend(direct)

if sale_mode in ("auction", "both"):
    auction = await scrape_sale_type(
        session, max_ton, filters,
        NFT_QUERY_AUCTION, "auction", seen,
        page_offset=len(all_results) // 100, progress_cb=progress_cb
    )
    all_results.extend(auction)

all_results.sort(key=lambda x: (x[1], x[0]))
return all_results
```

class ScrapeStates(StatesGroup):
waiting_for_filters = State()
waiting_for_sale_mode = State()
waiting_for_max_ton = State()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

@dp.message(Command(“start”, “help”))
async def cmd_start(message: Message):
await message.answer(
“<b>GetGems Username Scraper</b>\n\n”
“Scrapes getgems.io for Telegram usernames that are real words.\n\n”
“Commands:\n”
“/scrape - start a new scrape\n”
“/help - show this message”,
parse_mode=“HTML”
)

@dp.message(Command(“scrape”))
async def cmd_scrape(message: Message, state: FSMContext):
await state.set_state(ScrapeStates.waiting_for_filters)
await message.answer(
“<b>Step 1 - Choose word filters</b>\n\n”
“Reply with one or more options separated by commas:\n\n”
“english - English dictionary words\n”
“spanish - Spanish dictionary words\n”
“og - OG handles (4 chars or less)\n\n”
“Example: english, og\n”
“Or just: english”,
parse_mode=“HTML”
)

@dp.message(ScrapeStates.waiting_for_filters)
async def handle_filters(message: Message, state: FSMContext):
raw = message.text.lower()
filters = set()
if “english” in raw:
filters.add(“english”)
if “spanish” in raw:
filters.add(“spanish”)
if “og” in raw:
filters.add(“og”)

```
if not filters:
    await message.answer(
        "Couldn't parse that. Reply with english, spanish, og or a combo."
    )
    return

await state.update_data(filters=list(filters))
await state.set_state(ScrapeStates.waiting_for_sale_mode)

filter_labels = ", ".join(sorted(filters))
await message.answer(
    "Filters set: <b>" + filter_labels + "</b>\n\n"
    "<b>Step 2 - Sale type</b>\n\n"
    "Which listings do you want to include?\n\n"
    "direct - fixed price / buy now only\n"
    "auction - auctions only\n"
    "both - all listings\n\n"
    "Reply with one of the above.",
    parse_mode="HTML"
)
```

@dp.message(ScrapeStates.waiting_for_sale_mode)
async def handle_sale_mode(message: Message, state: FSMContext):
raw = message.text.strip().lower()

```
if raw in ("direct", "fix", "fixed", "buy now", "buynow"):
    sale_mode = "direct"
elif raw in ("auction", "auctions", "bid"):
    sale_mode = "auction"
elif raw in ("both", "all"):
    sale_mode = "both"
else:
    await message.answer(
        "Please reply with direct, auction, or both."
    )
    return

await state.update_data(sale_mode=sale_mode)
await state.set_state(ScrapeStates.waiting_for_max_ton)

await message.answer(
    "Sale type: <b>" + sale_mode + "</b>\n\n"
    "<b>Step 3 - Max TON price</b>\n\n"
    "What's the max TON price per username to include?\n"
    "Reply with a number (e.g. 10 or 50.5)\n"
    "Or 0 / any for no limit.",
    parse_mode="HTML"
)
```

@dp.message(ScrapeStates.waiting_for_max_ton)
async def handle_max_ton(message: Message, state: FSMContext):
text = message.text.strip().lower()
max_ton = None

```
if text in ("0", "any", "none", "no limit", "unlimited"):
    max_ton = None
else:
    try:
        max_ton = float(text)
        if max_ton <= 0:
            max_ton = None
    except ValueError:
        await message.answer("Please send a valid number like 10 or 0 for no limit.")
        return

data = await state.get_data()
filters = data.get("filters", ["english"])
sale_mode = data.get("sale_mode", "both")
await state.clear()

filter_labels = ", ".join(sorted(filters))
price_label = str(max_ton) + " TON" if max_ton else "no limit"

status_msg = await message.answer(
    "<b>Starting scrape...</b>\n\n"
    "Filters: <b>" + filter_labels + "</b>\n"
    "Sale type: <b>" + sale_mode + "</b>\n"
    "Max price: <b>" + price_label + "</b>\n\n"
    "This can take a few minutes. Updating every 5 pages.",
    parse_mode="HTML"
)

async def progress_cb(page, found):
    try:
        await bot.edit_message_text(
            "<b>Scraping...</b> page " + str(page) + " | found <b>" + str(found) + "</b> matches so far",
            chat_id=status_msg.chat.id,
            message_id=status_msg.message_id,
            parse_mode="HTML"
        )
    except Exception:
        pass

async with aiohttp.ClientSession() as session:
    results = await scrape_getgems(session, max_ton, filters, sale_mode, progress_cb)

if not results:
    await message.answer(
        "No matching usernames found. Try broadening the filters or increasing the max TON."
    )
    return

lines = ["word,mint_price_ton,sale_type"]
for username, price, sale_label in results:
    lines.append(username + "," + str(price) + "," + sale_label)
content = "\n".join(lines)

filter_tag = "_".join(sorted(filters))
price_tag = "max" + str(int(max_ton)) + "ton" if max_ton else "nolimit"
filename = "getgems_" + filter_tag + "_" + sale_mode + "_" + price_tag + ".txt"

with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
    tmp.write(content)
    tmp_path = tmp.name

try:
    await message.answer_document(
        FSInputFile(tmp_path, filename=filename),
        caption=(
            "<b>Scrape complete!</b>\n\n"
            "Found <b>" + str(len(results)) + "</b> usernames\n"
            "Filters: <b>" + filter_labels + "</b>\n"
            "Sale type: <b>" + sale_mode + "</b>\n"
            "Max price: <b>" + price_label + "</b>"
        ),
        parse_mode="HTML"
    )
finally:
    os.unlink(tmp_path)
```

async def main():
await dp.start_polling(bot)

if **name** == “**main**”:
asyncio.run(main())