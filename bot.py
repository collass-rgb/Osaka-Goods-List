import discord
import aiohttp
import os
import json
import re
from anthropic import Anthropic

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GAS_WEBAPP_URL = os.environ.get("GAS_WEBAPP_URL")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

ai_client = Anthropic(api_key=ANTHROPIC_API_KEY)


def clean_content(text):
    return re.sub(r'<@!?\d+>', '', text).strip()


def looks_like_code(text):
    # 支援：A837、A1080-TK2、A1080-L、A260302
    # 條件：含數字、只有英數字和連字號、2碼以上
    if len(text) < 2:
        return False
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9\-]+$', text):
        return False
    if not re.search(r'\d', text):
        return False
    return True


@client.event
async def on_ready():
    print(f"Bot 已上線：{client.user} (ID: {client.user.id})")


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    content = clean_content(message.content)

    # AI 模式
    if content.startswith("AI "):
        query = content[3:].strip()
        if query:
            await handle_ai_query(message, query)
        return

    # 含「查」字
    if "查" in content:
        code = content.replace("查", "").strip()
        if code:
            await handle_structured_query(message, code)
        return

    # 直接輸入貨號（不轉大寫，保留原始格式）
    code = content.strip()
    if looks_like_code(code):
        await handle_structured_query(message, code)


async def handle_structured_query(message, code):
    await message.channel.send(f"🔍 查詢貨號 `{code}` 中...")

    try:
        async with aiohttp.ClientSession() as session:
            params = {"action": "query", "code": code}
            async with session.get(
                GAS_WEBAPP_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                raw = await resp.text()
                data = json.loads(raw)
    except Exception as e:
        await message.channel.send(f"❌ 無法連線到 GAS：{e}")
        return

    if "error" in data:
        await message.channel.send(f"❌ {data['error']}")
        return

    results = data.get("results", [])
    if not results:
        msg = data.get("message", f"找不到貨號 `{code}` 的資料。")
        await message.channel.send(f"📭 {msg}")
        return

    lines = [f"📦 **貨號 `{code}`** 查詢結果"]

    # 有多筆時顯示備註
    note = data.get("note", "")
    if note:
        lines.append(f"ℹ️ {note}")

    lines.append("─" * 28)

    for r in results:
        date = r['出口日']
        qty  = int(r['出貨個數'])
        box  = r['箱號']

        date_str = "⏳ 未排定" if date == "未排定" else f"📅 `{date}`"

        if r.get('分批') and r.get('分批總數', 0) > 0:
            qty_str = f"**{qty}**（分批，總數：{int(r['分批總數'])}）"
        else:
            qty_str = f"**{qty}**"

        lines.append(f"{date_str}　數量：{qty_str}　箱號：`{box}`")

    lines.append("─" * 28)
    total = data.get('total', 0)
    if total > 0:
        lines.append(f"✅ 出貨合計：**{int(total)} 個**")

    await send_in_chunks(message.channel, lines)


async def handle_ai_query(message, query):
    await message.channel.send("🤖 AI 分析中，請稍候...")

    gas_context = ""
    possible_code = query.split()[0] if query.split() else ""
    if possible_code and looks_like_code(possible_code):
        try:
            async with aiohttp.ClientSession() as session:
                params = {"action": "query", "code": possible_code}
                async with session.get(
                    GAS_WEBAPP_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    raw = await resp.text()
                    sheet_data = json.loads(raw)
                    if "results" in sheet_data and sheet_data["results"]:
                        gas_context = f"\n\n【試算表資料 - 貨號 {possible_code}】\n"
                        for r in sheet_data["results"]:
                            gas_context += (
                                f"出口日：{r['出口日']}，"
                                f"出貨個數：{r['出貨個數']}，"
                                f"箱號：{r['箱號']}\n"
                            )
                        gas_context += f"合計：{sheet_data.get('total', 0)} 個\n"
        except Exception:
            pass

    system_prompt = (
        "你是一個台日跨境貿易的助理，協助查詢貨物清單與通關資料。"
        "回覆請使用繁體中文，精簡扼要，200字以內。"
        "如果有試算表資料，請根據資料直接回答。"
    )

    try:
        response = ai_client.messages.create(
            model="claude-opus-4-5",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": query + gas_context}]
        )
        reply = response.content[0].text
    except Exception as e:
        await message.channel.send(f"❌ AI 回覆失敗：{e}")
        return

    await send_in_chunks(message.channel, reply.splitlines())


async def send_in_chunks(channel, lines, limit=1900):
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            if current:
                await channel.send(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        await channel.send(current)


client.run(DISCORD_TOKEN)
