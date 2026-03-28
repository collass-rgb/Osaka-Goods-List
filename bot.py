import discord
import aiohttp
import os
import json
from anthropic import Anthropic

# ── 環境變數
DISCORD_TOKEN     = os.environ.get("DISCORD_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GAS_WEBAPP_URL    = os.environ.get("GAS_WEBAPP_URL")

# ── Discord Client（必須開啟 message_content intent）
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ── Anthropic Client
ai_client = Anthropic(api_key=ANTHROPIC_API_KEY)


# ── 啟動事件
@client.event
async def on_ready():
    print(f"Bot 已上線：{client.user} (ID: {client.user.id})")


# ── 訊息事件
@client.event
async def on_message(message):
    # 忽略 Bot 自己的訊息
    if message.author == client.user:
        return

    content = message.content.strip()

    # ── AI 模式：「AI 」開頭，呼叫 Claude
if content.startswith("AI ") or re.sub(r'<@!?\d+>', '', content).strip().startswith("AI "):
    import re
    cleaned = re.sub(r'<@!?\d+>', '', content).strip()
    query = cleaned[3:].strip() if cleaned.startswith("AI ") else cleaned
        if query:
            await handle_ai_query(message, query)
        return

if "查" in content:
    # 清除 @ mention 標記（格式為 <@數字>）
    import re
    cleaned = re.sub(r'<@!?\d+>', '', content).strip()
    code = cleaned.replace("查", "").strip().upper()


# =====================================================================================
# 制式查詢：呼叫 GAS，免費
# =====================================================================================

async def handle_structured_query(message, code):
    await message.channel.send(f"🔍 查詢貨號 `{code}` 中...")

    try:
        async with aiohttp.ClientSession() as session:
            params = {"action": "query", "code": code}
            async with session.get(GAS_WEBAPP_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                raw = await resp.text()
                data = json.loads(raw)
    except Exception as e:
        await message.channel.send(f"❌ 無法連線到 GAS：{e}")
        return

    # ── 錯誤處理
    if "error" in data:
        await message.channel.send(f"❌ {data['error']}")
        return

    results = data.get("results", [])
    if not results:
        msg = data.get("message", f"找不到貨號 `{code}` 的資料。")
        await message.channel.send(f"📭 {msg}")
        return

    # ── 組成回覆文字
    lines = [f"📦 **貨號 `{code}`** 查詢結果"]
    lines.append("─" * 28)
    for r in results:
        lines.append(
            f"📅 `{r['出口日']}`　"
            f"數量：**{int(r['出貨個數'])}**　"
            f"箱號：`{r['箱號']}`"
        )
    lines.append("─" * 28)
    lines.append(f"✅ 合計出貨：**{int(data.get('total', 0))} 個**")

    # ── 分批發送（Discord 上限 2000 字元）
    await send_in_chunks(message.channel, lines)


# =====================================================================================
# AI 查詢：呼叫 Claude，需 ANTHROPIC_API_KEY
# =====================================================================================

async def handle_ai_query(message, query):
    await message.channel.send("🤖 AI 分析中，請稍候...")

    # ── 先把 GAS 資料拉進來，讓 Claude 有 context
    gas_context = ""
    if "查" in query or any(c.isalnum() for c in query):
        # 嘗試從問題中提取貨號
        possible_code = query.split()[0] if query.split() else ""
        if possible_code:
            try:
                async with aiohttp.ClientSession() as session:
                    params = {"action": "query", "code": possible_code}
                    async with session.get(GAS_WEBAPP_URL, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                        raw = await resp.text()
                        sheet_data = json.loads(raw)
                        if "results" in sheet_data and sheet_data["results"]:
                            gas_context = f"\n\n【試算表資料 - 貨號 {possible_code}】\n"
                            for r in sheet_data["results"]:
                                gas_context += f"出口日：{r['出口日']}，出貨個數：{r['出貨個數']}，箱號：{r['箱號']}\n"
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
            messages=[
                {"role": "user", "content": query + gas_context}
            ]
        )
        reply = response.content[0].text
    except Exception as e:
        await message.channel.send(f"❌ AI 回覆失敗：{e}")
        return

    # ── 分批發送
    await send_in_chunks(message.channel, reply.splitlines())


# =====================================================================================
# 工具：分批發送（每則最多 1900 字元，預留 buffer）
# =====================================================================================

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


# ── 啟動
client.run(DISCORD_TOKEN)
