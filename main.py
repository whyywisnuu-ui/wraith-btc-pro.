
import os
import time
import io
import threading
import logging
from datetime import datetime, timezone

import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as patches
from flask import Flask

# ==========================================
# KONFIGURASI
# Token dan ID diambil dari Environment Render
# ==========================================
BOT_TOKEN = os.getenv("8813618383:AAG-CL6gyWDKSnK4dxl8yNePyS1kBSfvFkc", "").strip()
CHAT_ID = os.getenv("5804918716", "").strip()

if not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError(
        "Atur BOT_TOKEN dan CHAT_ID pada Environment Render."
    )

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
BASE = "https://contract.mexc.com/api/v1/contract"
SYMBOL = "BTC_USDT"

INTERVAL_KIRIM = 300  # 5 menit
TIMEOUT = 30
AUTO_UPDATE = True

TFS = {
    "M1": ("Min1", 60),
    "M5": ("Min5", 300),
    "M15": ("Min15", 900),
    "M30": ("Min30", 1800),
    "H1": ("Min60", 3600),
    "H4": ("Hour4", 14400),
    "H8": ("Hour8", 28800),
    "D1": ("Day1", 86400),
    "W1": ("Week1", 604800),
    "MN1": ("Month1", 2592000),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("wraith")

S = requests.Session()
app = Flask(__name__)


# ==========================================
# WEB SERVICE RENDER
# ==========================================
@app.route("/")
def home():
    return "WRAITH BTC PRO berjalan.", 200


@app.route("/health")
def health():
    return {"status": "ok", "bot": "WRAITH BTC PRO"}, 200


# ==========================================
# TELEGRAM
# ==========================================
def tg(method, payload=None, files=None):
    url = f"{API}/{method}"

    if files:
        response = requests.post(
            url, data=payload, files=files, timeout=TIMEOUT
        )
    else:
        response = requests.post(
            url, json=payload or {}, timeout=TIMEOUT
        )

    response.raise_for_status()
    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(str(data))

    return data


def send(text, keyboard=None):
    if len(text) > 3900:
        text = text[:3750] + "\n\nLaporan dipotong."

    payload = {"chat_id": CHAT_ID, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard

    return tg("sendMessage", payload)


def send_photo(image_bytes, caption):
    return tg(
        "sendPhoto",
        {"chat_id": CHAT_ID, "caption": caption[:1000]},
        files={
            "photo": (
                "wraith_btc.png",
                image_bytes,
                "image/png"
            )
        }
    )


def kb(rows):
    return {
        "inline_keyboard": [
            [
                {"text": label, "callback_data": command}
                for label, command in row
            ]
            for row in rows
        ]
    }


def main_menu():
    return kb([
        [("📊 Analisis Semua", "ALL"),
         ("🎯 Cari Sinyal", "SIGNALS")],
        [("📷 Grafik H4", "CH_H4"),
         ("📷 Grafik H1", "CH_H1")],
        [("🕒 Pilih Grafik TF", "TFMENU"),
         ("📈 Tren Semua TF", "TREND")],
        [("🧱 Support/Resistance", "SR"),
         ("⚡ Momentum", "MOM")],
        [("🔔 Pengaturan Auto", "AUTO"),
         ("📡 Status Bot", "STATUS")],
        [("📚 Strategi", "STRATEGY"),
         ("🛡️ Risiko", "RISK")]
    ])


def tf_menu():
    return kb([
        [("M1", "CH_M1"), ("M5", "CH_M5"), ("M15", "CH_M15")],
        [("M30", "CH_M30"), ("H1", "CH_H1"), ("H4", "CH_H4")],
        [("H8", "CH_H8"), ("D1", "CH_D1")],
        [("W1", "CH_W1"), ("MN1", "CH_MN1")],
        [("⬅️ Menu", "HOME")]
    ])


def auto_menu():
    return kb([
        [("🔔 Auto ON", "AUTO_ON"),
         ("🔕 Auto OFF", "AUTO_OFF")],
        [("⬅️ Menu", "HOME")]
    ])


# ==========================================
# DATA HARGA MEXC
# ==========================================
def live_price():
    response = S.get(
        f"{BASE}/ticker",
        params={"symbol": SYMBOL},
        timeout=TIMEOUT
    )
    response.raise_for_status()
    data = response.json().get("data")

    if isinstance(data, list):
        data = next(
            (item for item in data if item.get("symbol") == SYMBOL),
            data[0] if data else None
        )

    if not isinstance(data, dict):
        raise RuntimeError("Ticker MEXC tidak tersedia.")

    for key in ("lastPrice", "last_price", "fairPrice"):
        if data.get(key) is not None:
            return float(data[key])

    raise RuntimeError("Harga ticker tidak ditemukan.")


def get_candles(tf):
    interval, seconds = TFS[tf]
    now = int(time.time())

    response = S.get(
        f"{BASE}/kline/{SYMBOL}",
        params={
            "interval": interval,
            "start": now - seconds * 260,
            "end": now
        },
        timeout=TIMEOUT
    )
    response.raise_for_status()
    data = response.json().get("data")

    if not isinstance(data, dict) or not data.get("time"):
        raise RuntimeError("Data candle MEXC kosong.")

    df = pd.DataFrame({
        "time": data["time"],
        "open": data["open"],
        "high": data["high"],
        "low": data["low"],
        "close": data["close"]
    })

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["time"] = pd.to_datetime(
        pd.to_numeric(df["time"]),
        unit="s",
        utc=True
    )

    df = (
        df.dropna()
        .drop_duplicates("time")
        .sort_values("time")
        .reset_index(drop=True)
    )

    if len(df) < 205:
        raise RuntimeError(
            f"Candle {tf} kurang: {len(df)}."
        )

    return df


# ==========================================
# INDIKATOR
# ==========================================
def indicators(df):
    df = df.copy()

    for period in (20, 50, 200):
        df[f"EMA{period}"] = df["close"].ewm(
            span=period, adjust=False
        ).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1/14, min_periods=14, adjust=False
    ).mean()
    avg_loss = loss.ewm(
        alpha=1/14, min_periods=14, adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["RSI"] = (100 - 100 / (1 + rs)).fillna(50)

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACDS"] = df["MACD"].ewm(
        span=9, adjust=False
    ).mean()

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)

    df["ATR"] = tr.ewm(
        alpha=1/14, adjust=False
    ).mean()

    return df


# ==========================================
# ANALISIS MULTI-TIMEFRAME
# ==========================================
def analyze(tf):
    raw = get_candles(tf)
    price = live_price()
    df = indicators(raw)

    # Mengabaikan candle paling akhir yang diasumsikan berjalan.
    closed = df.iloc[:-1].copy()

    if len(closed) < 200:
        raise RuntimeError("Riwayat candle belum cukup.")

    row = closed.iloc[-1]
    prev = closed.iloc[-2]

    e20 = float(row["EMA20"])
    e50 = float(row["EMA50"])
    e200 = float(row["EMA200"])
    rsi = float(row["RSI"])
    macd = float(row["MACD"])
    macds = float(row["MACDS"])
    atr = float(row["ATR"])

    if e20 > e50 > e200:
        trend = "BULLISH KUAT"
    elif e20 > e50:
        trend = "BULLISH"
    elif e20 < e50 < e200:
        trend = "BEARISH KUAT"
    elif e20 < e50:
        trend = "BEARISH"
    else:
        trend = "SIDEWAYS"

    hist = closed.tail(105).reset_index(drop=True)
    highs, lows = [], []

    for i in range(2, len(hist) - 2):
        if hist["high"].iloc[i] == hist["high"].iloc[i-2:i+3].max():
            highs.append(float(hist["high"].iloc[i]))
        if hist["low"].iloc[i] == hist["low"].iloc[i-2:i+3].min():
            lows.append(float(hist["low"].iloc[i]))

    supports = sorted(
        [v for v in lows if v < price], reverse=True
    )
    resistances = sorted(v for v in highs if v > price)

    support = supports[0] if supports else float(hist["low"].min())
    resistance = (
        resistances[0] if resistances
        else float(hist["high"].max())
    )

    zone = max(atr * 0.35, price * 0.0003)

    bounce_buy = (
        abs(float(row["low"]) - support) <= zone
        and float(row["close"]) > float(row["open"])
        and float(row["close"]) > float(prev["close"])
    )
    bounce_sell = (
        abs(float(row["high"]) - resistance) <= zone
        and float(row["close"]) < float(row["open"])
        and float(row["close"]) < float(prev["close"])
    )

    base = closed.iloc[:-3].tail(50)
    recent = closed.tail(3)
    old_res = float(base["high"].max())
    old_sup = float(base["low"].min())
    before = recent.iloc[-2]
    last = recent.iloc[-1]

    retest_buy = (
        float(before["close"]) > old_res
        and float(last["low"]) <= old_res + zone
        and float(last["close"]) > old_res
        and float(last["close"]) > float(last["open"])
    )
    retest_sell = (
        float(before["close"]) < old_sup
        and float(last["high"]) >= old_sup - zone
        and float(last["close"]) < old_sup
        and float(last["close"]) < float(last["open"])
    )

    mom_buy = (
        e20 > e50 and price > e20
        and 50 <= rsi <= 70 and macd > macds
    )
    mom_sell = (
        e20 < e50 and price < e20
        and 30 <= rsi <= 50 and macd < macds
    )

    signal = "NO TRADE"
    setup = "Belum ada setup"
    entry = sl = tp1 = tp2 = tp3 = None
    risk = atr * 1.5

    if atr > 0 and mom_buy and (bounce_buy or retest_buy):
        if resistance - price >= risk * 0.8:
            signal = "BUY"
            setup = (
                "Breakout/retest" if retest_buy
                else "Pantulan support"
            )
            entry = price
            sl = entry - risk
            tp1 = entry + risk
            tp2 = entry + 2 * risk
            tp3 = entry + 3 * risk
            if resistance > entry:
                tp1 = min(tp1, resistance)

    elif atr > 0 and mom_sell and (bounce_sell or retest_sell):
        if price - support >= risk * 0.8:
            signal = "SELL"
            setup = (
                "Breakdown/retest" if retest_sell
                else "Penolakan resistance"
            )
            entry = price
            sl = entry + risk
            tp1 = entry - risk
            tp2 = entry - 2 * risk
            tp3 = entry - 3 * risk
            if support < entry:
                tp1 = max(tp1, support)

    return {
        "tf": tf, "df": df, "price": price, "trend": trend,
        "rsi": rsi, "macd": macd, "macds": macds, "atr": atr,
        "support": support, "resistance": resistance,
        "signal": signal, "setup": setup,
        "entry": entry, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "candle_time": str(row["time"])
    }


# ==========================================
# GRAFIK
# ==========================================
def make_chart(x):
    df = x["df"].tail(100).copy()
    df["date_num"] = mdates.date2num(
        df["time"].dt.tz_convert(None)
    )

    fig, (ax, ar, am) = plt.subplots(
        3, 1, figsize=(14, 10),
        gridspec_kw={"height_ratios": [3, 1, 1]}
    )
    fig.patch.set_facecolor("#10151f")

    for axis in (ax, ar, am):
        axis.set_facecolor("#10151f")
        axis.tick_params(colors="white", labelsize=8)
        axis.grid(True, alpha=0.18)
        for spine in axis.spines.values():
            spine.set_color("#566174")
        axis.yaxis.tick_right()

    dates = df["date_num"].to_numpy()
    width = (
        max((dates[-1] - dates[-2]) * 0.65, 0.00001)
        if len(dates) > 1 else 0.0005
    )

    for _, candle in df.iterrows():
        up = candle["close"] >= candle["open"]
        color = "#20d6a1" if up else "#ff5d73"

        ax.vlines(
            candle["date_num"], candle["low"], candle["high"],
            color=color, linewidth=0.8
        )
        lower = min(candle["open"], candle["close"])
        height = max(abs(candle["close"] - candle["open"]), 0.01)

        ax.add_patch(patches.Rectangle(
            (candle["date_num"] - width / 2, lower),
            width, height,
            facecolor=color, edgecolor=color, linewidth=0.5
        ))

    ax.plot(dates, df["EMA20"], color="#00c8ff", label="EMA20")
    ax.plot(dates, df["EMA50"], color="#ffca58", label="EMA50")
    ax.plot(dates, df["EMA200"], color="#da86ff", label="EMA200")
    ax.axhline(
        x["price"], color="white", linestyle="--",
        label=f"Ticker {x['price']:.2f}"
    )
    ax.axhline(
        x["support"], color="#20d6a1", linestyle=":",
        label=f"Support {x['support']:.2f}"
    )
    ax.axhline(
        x["resistance"], color="#ff5d73", linestyle=":",
        label=f"Resistance {x['resistance']:.2f}"
    )

    if x["signal"] in ("BUY", "SELL"):
        for key, color in [
            ("entry", "#00c8ff"), ("sl", "#ff5d73"),
            ("tp1", "#20d6a1"), ("tp2", "#20d6a1"),
            ("tp3", "#20d6a1")
        ]:
            value = x[key]
            ax.axhline(value, color=color, linestyle="--", linewidth=0.9)
            ax.text(
                dates[-1], value, f" {key.upper()} {value:.1f}",
                color=color, fontsize=7, va="bottom"
            )

    ax.set_title(
        f"BTCUSDT PERPETUAL | {x['tf']} | {x['signal']}",
        color="white", fontsize=13, fontweight="bold"
    )
    ax.legend(
        loc="upper left", fontsize=7, ncol=2,
        facecolor="#182333", labelcolor="white"
    )
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))

    ar.plot(dates, df["RSI"], color="#ffca58")
    ar.axhline(70, color="#ff5d73", linestyle="--", linewidth=0.7)
    ar.axhline(30, color="#20d6a1", linestyle="--", linewidth=0.7)
    ar.axhline(50, color="white", linestyle=":", linewidth=0.5)
    ar.set_ylim(0, 100)
    ar.set_ylabel("RSI 14", color="white")

    am.plot(dates, df["MACD"], color="#00c8ff", label="MACD")
    am.plot(dates, df["MACDS"], color="#ffca58", label="Signal")
    am.axhline(0, color="white", linewidth=0.5)
    am.set_ylabel("MACD", color="white")
    am.legend(
        loc="upper left", fontsize=7,
        facecolor="#182333", labelcolor="white"
    )
    am.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))

    fig.text(
        0.02, 0.01,
        "MEXC live ticker | Indicators use presumed closed candles | "
        + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        color="white", fontsize=8
    )
    fig.tight_layout(rect=[0, 0.03, 1, 1])

    buffer = io.BytesIO()
    fig.savefig(
        buffer, format="png", dpi=120,
        facecolor=fig.get_facecolor()
    )
    plt.close(fig)
    buffer.seek(0)
    return buffer


# ==========================================
# LAPORAN
# ==========================================
def format_result(x):
    lines = [
        f"📊 BTCUSDT | {x['tf']}",
        f"Harga ticker: {x['price']:.2f}",
        f"Tren: {x['trend']}",
        f"RSI: {x['rsi']:.2f}",
        f"MACD: {x['macd']:.2f}",
        f"Support: {x['support']:.2f}",
        f"Resistance: {x['resistance']:.2f}",
        f"Sinyal: {x['signal']}",
        f"Setup: {x['setup']}",
    ]

    if x["signal"] in ("BUY", "SELL"):
        lines.extend([
            f"Entry: {x['entry']:.2f}",
            f"SL: {x['sl']:.2f}",
            f"TP1: {x['tp1']:.2f}",
            f"TP2: {x['tp2']:.2f}",
            f"TP3: {x['tp3']:.2f}",
        ])

    lines.extend([
        f"Candle analisis: {x['candle_time']}",
        "",
        "Harga ticker terbaru; indikator memakai candle sebelumnya.",
        "Bukan jaminan profit."
    ])
    return "\n".join(lines)


def send_chart(tf):
    x = analyze(tf)
    img = make_chart(x)
    caption = (
        f"📈 BTCUSDT | {tf}\n"
        f"Harga ticker: {x['price']:.2f}\n"
        f"Tren: {x['trend']} | Sinyal: {x['signal']}\n"
        f"Setup: {x['setup']}\n"
        f"Support: {x['support']:.2f}\n"
        f"Resistance: {x['resistance']:.2f}\n"
        "Indikator memakai candle yang diasumsikan tertutup."
    )
    send_photo(img.getvalue(), caption)
    send(format_result(x), main_menu())


def all_report():
    lines = ["🧠 WRAITH BTC PRO", "Harga ticker terbaru per analisis."]
    for tf in TFS:
        try:
            lines.extend(["", format_result(analyze(tf))])
        except Exception as error:
            lines.append(f"\n⚠️ {tf}: {str(error)[:100]}")
    return "\n".join(lines)


def signal_report():
    lines = ["🎯 SINYAL BTC", "━━━━━━━━━━━━━━━━"]
    found = 0
    for tf in TFS:
        try:
            x = analyze(tf)
            if x["signal"] in ("BUY", "SELL"):
                found += 1
                lines.extend(["", format_result(x)])
        except Exception as error:
            log.warning("%s: %s", tf, error)

    if found == 0:
        lines.append("\nBelum ada sinyal yang memenuhi filter.")
    return "\n".join(lines)


# ==========================================
# MENU HANDLER
# ==========================================
def handle(update):
    global AUTO_UPDATE

    msg = update.get("message")
    callback = update.get("callback_query")

    if msg:
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id != CHAT_ID:
            return

        text = (msg.get("text") or "").strip()
        command = text.split()[0].split("@")[0].lower() if text else ""

        if command in ("/start", "/menu"):
            send("🤖 WRAITH BTC PRO\nPilih fitur:", main_menu())
        elif command == "/analisis":
            send(all_report(), main_menu())
        elif command == "/sinyal":
            send(signal_report(), main_menu())
        elif command == "/status":
            send(
                f"📡 Bot berjalan\nAuto update: {AUTO_UPDATE}\n"
                f"Interval: {INTERVAL_KIRIM // 60} menit",
                main_menu()
            )
        elif command == "/auto":
            AUTO_UPDATE = not AUTO_UPDATE
            send(f"Auto update: {AUTO_UPDATE}", main_menu())
        elif command == "/tf" and len(text.split()) > 1:
            tf = text.split()[1].upper()
            if tf in TFS:
                send_chart(tf)
            else:
                send("Timeframe tidak valid.", tf_menu())
        else:
            send("Ketik /start untuk membuka menu.", main_menu())
        return

    if not callback:
        return

    callback_id = callback.get("id")
    chat_id = str(
        callback.get("message", {}).get("chat", {}).get("id", "")
    )

    if chat_id != CHAT_ID:
        try:
            tg("answerCallbackQuery", {
                "callback_query_id": callback_id,
                "text": "Akses ditolak."
            })
        except Exception:
            pass
        return

    action = callback.get("data", "")

    try:
        tg("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "Memproses..."
        })
    except Exception:
        pass

    if action == "HOME":
        send("🏠 MENU UTAMA", main_menu())
    elif action == "ALL":
        send(all_report(), main_menu())
    elif action == "SIGNALS":
        send(signal_report(), main_menu())
    elif action == "TFMENU":
        send("Pilih timeframe grafik:", tf_menu())
    elif action.startswith("CH_"):
        tf = action[3:]
        if tf in TFS:
            send_chart(tf)
    elif action == "TREND":
        lines = ["📈 TREN MULTI-TIMEFRAME"]
        for tf in TFS:
            try:
                x = analyze(tf)
                lines.append(f"{tf}: {x['trend']} | {x['signal']}")
    
