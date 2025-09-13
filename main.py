import os
import logging
import discord
import asyncio
import pandas as pd
from binance.client import Client
from datetime import datetime
import pytz
import nest_asyncio
from typing import cast
from flask import Flask
from threading import Thread

# Enable logging
logging.basicConfig(level=logging.INFO)

# --- CONFIG ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1411642002319347784
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

if not all([DISCORD_TOKEN, BINANCE_API_KEY, BINANCE_API_SECRET]):
    raise ValueError("❌ Missing environment variables! Please set DISCORD_TOKEN, BINANCE_API_KEY, BINANCE_API_SECRET.")

client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

TIMEFRAME = "1d"
KLIMIT = 150
SLEEP_SECONDS = 3600
MAX_FIELD_CHAR = 1000
MAX_EMBED_FIELDS = 20
REQUEST_SLEEP = 0.05

# --- Load SPOT Symbols (instead of futures) ---
exchange_info = client.get_exchange_info()
spot_symbols = [
    s["symbol"] for s in exchange_info["symbols"]
    if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
]
logging.info(f"{len(spot_symbols)} USDT spot symbols loaded.")

# --- Indicator calculation ---
def get_indicators(symbol: str):
    try:
        candles = client.get_klines(symbol=symbol, interval=TIMEFRAME, limit=KLIMIT)
        if not candles:
            return None
        column_names = [
            'time', 'open', 'high', 'low', 'close',
            'volume', 'close_time', 'quote_asset_volume',
            'num_trades', 'taker_buy_base', 'taker_buy_quote', 'ignore'
        ]
        df = pd.DataFrame(candles, columns=column_names)
        df['close'] = df['close'].astype(float)
        df['MA20'] = df['close'].rolling(20, min_periods=1).mean()
        df['MA50'] = df['close'].rolling(50, min_periods=1).mean()
        df['MA100'] = df['close'].rolling(100, min_periods=1).mean()
        return df.iloc[-1]
    except Exception as e:
        logging.debug(f"get_indicators({symbol}) error: {e}")
        return None

def is_aots(symbol: str):
    latest = get_indicators(symbol)
    if latest is None:
        return False
    try:
        return latest['MA20'] > latest['MA50'] > latest['MA100']
    except Exception:
        return False

# --- Helpers ---
def chunk_symbols_into_fields(symbols, prefix="🟢 "):
    fields, curr_list, curr_len, processed_count = [], [], len(prefix), 0
    for sym in symbols:
        token = (", " if curr_list else "") + sym
        if curr_len + len(token) <= MAX_FIELD_CHAR:
            curr_list.append(sym)
            curr_len += len(token)
            processed_count += 1
        else:
            fields.append(prefix + ", ".join(curr_list))
            curr_list, curr_len = [sym], len(prefix) + len(sym)
            processed_count += 1
            if len(fields) >= MAX_EMBED_FIELDS:
                break
    if curr_list and len(fields) < MAX_EMBED_FIELDS:
        fields.append(prefix + ", ".join(curr_list))
    remaining = len(symbols) - processed_count
    if remaining > 0:
        note = f"...and {remaining} more"
        if len(fields) < MAX_EMBED_FIELDS:
            fields.append(note)
        else:
            fields[-1] += "\n" + note
    return fields

# --- Discord Bot ---
intents = discord.Intents.default()
bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    logging.info(f"✅ Bot logged in as {bot.user}")
    channel = cast(discord.TextChannel, bot.get_channel(CHANNEL_ID))
    if channel is None:
        logging.error(f"❌ Channel with ID {CHANNEL_ID} not found or bot lacks access.")
        return

    while True:
        try:
            tier2 = []
            for sym in spot_symbols:
                if is_aots(sym):
                    tier2.append(sym.replace("USDT", ""))
                await asyncio.sleep(REQUEST_SLEEP)  # avoid API bans

            manila = pytz.timezone("Asia/Manila")
            now = datetime.now(manila).strftime("%I:%M:%S %p Manila Time")

            embed = discord.Embed(
                title=f"Automated AOTS Spot Setups — {now}",
                description="**🎯 Tier 2 (AOTS)**\n20MA > 50MA > 100MA\nUSDT Spot Market",
                color=0x00ff00
            )

            if tier2:
                fields = chunk_symbols_into_fields(tier2, prefix="🟢 ")
                for idx, val in enumerate(fields):
                    name = f"TIER 2 (part {idx+1})" if len(fields) > 1 else "TIER 2"
                    embed.add_field(name=name, value=val, inline=False)
            else:
                embed.add_field(name="TIER 2", value="No setups detected.", inline=False)

            await channel.send(embed=embed)

        except Exception as e:
            logging.exception("Unexpected error during scan loop.")

        await asyncio.sleep(SLEEP_SECONDS)

# --- Flask Uptime Server ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_web():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

# --- Main execution ---
def start():
    # Apply nest_asyncio to allow asyncio to be re-entered
    nest_asyncio.apply()

    # Get the event loop
    loop = asyncio.get_event_loop()

    # Create a task for the bot
    bot_task = loop.create_task(bot.start(DISCORD_TOKEN))

    # Run both the Flask server and the bot concurrently
    try:
        loop.run_until_complete(bot_task)
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(bot.close())

if __name__ == "__main__":
    # Start the Flask server in a separate thread
    web_thread = Thread(target=run_web)
    web_thread.start()
    
    # Start the bot
    start()
