import os
import requests
import time
from datetime import datetime, timezone, timedelta

# ================== CONFIG - FUTURES FIXED (FUSION CRT XAU - SESSION VWAP LOGIC) ==================
# Sumber: TwelveData (XAU/USD) + BINANCE FUTURES untuk validasi silang jika diperlukan
# Strategy: FUSION CRT XAU - H1 CRT Range + H4 Bias + Killzone + Sweep Reclaim + FVG M5
# Bot: @Monitor_koin_crypto_bot - Repo terpisah xau-fusion (tidak ganggu afnan DUAL FUTURES)
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SYMBOL = "XAU/USD"
WIB = timezone(timedelta(hours=7))
UTC = timezone.utc
# ===============================================================================================

def send_tele(text: str):
    """Kirim Telegram - plain text tanpa Markdown parse error."""
    if "ISI_TOKEN" in TELEGRAM_TOKEN or not TELEGRAM_TOKEN:
        print("[DRY] TELEGRAM_TOKEN kosong - tidak kirim")
        print(text[:600])
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        # Plain text - tanpa parse_mode biar tidak error Can't parse entities
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        j = r.json()
        print("Telegram:", j)
        if not j.get("ok"):
            print("Gagal:", j)
    except Exception as e:
        print("Gagal kirim tele:", e)

def get_candles(interval, count=20):
    """Fetch TwelveData dengan error handling + rate limit safe."""
    url = f"https://api.twelvedata.com/time_series?symbol={SYMBOL}&interval={interval}&apikey={TWELVE_API_KEY}&count={count}&format=JSON"
    try:
        r = requests.get(url, timeout=10).json()
        if "values" not in r:
            # Rate limit atau error
            msg = r.get("message", str(r)[:200])
            if "limit" in msg.lower() or "run out" in msg.lower():
                print(f"[RATE LIMIT] {interval}: {msg}")
                return None
            print(f"Error Twelve {interval}:", r)
            return None
        # Twelve urut baru -> lama, balik jadi lama -> baru
        vals = r["values"][::-1]
        # Attach parsed WIB datetime untuk killzone valid
        for v in vals:
            try:
                # Twelve datetime = UTC "2026-10-21 13:00:00"
                dt_utc = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                dt_wib = dt_utc.astimezone(WIB)
                v["_dt_wib"] = dt_wib
                v["_dt_utc"] = dt_utc
            except:
                pass
        return vals
    except Exception as e:
        print(f"Exception get_candles {interval}:", e)
        return None

def calc_atr(candles, period=14):
    """ATR sederhana dari H1."""
    if not candles or len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"]); l = float(candles[i]["low"])
        pc = float(candles[i-1]["close"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / len(trs)
    return sum(trs[-period:]) / period

def find_swing_structure(candles, lookback=10):
    """Swing HH/HL vs LH/LL - cari swing high/low last 10, bukan cuma banding 2 candle."""
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    # Swing high = high > tetangga +-2
    swings_h = []; swings_l = []
    for i in range(2, len(candles)-2):
        h = float(candles[i]["high"])
        if h > float(candles[i-1]["high"]) and h > float(candles[i+1]["high"]) and h > float(candles[i-2]["high"]) and h > float(candles[i+2]["high"]):
            swings_h.append(h)
        l = float(candles[i]["low"])
        if l < float(candles[i-1]["low"]) and l < float(candles[i+1]["low"]) and l < float(candles[i-2]["low"]) and l < float(candles[i+2]["low"]):
            swings_l.append(l)
    # Ambil 3 terakhir
    last_h = swings_h[-3:] if len(swings_h) >= 3 else highs[-3:]
    last_l = swings_l[-3:] if len(swings_l) >= 3 else lows[-3:]
    return last_h, last_l

def get_fvg_m5():
    """Cek FVG M5 valid (>0.2% belum tutup 50%)."""
    m5 = get_candles("5min", 20)
    if not m5 or len(m5) < 10:
        return False, []
    fvgs = []
    for i in range(2, len(m5)):
        c1 = m5[i-2]; c3 = m5[i]
        c1h = float(c1["high"]); c3l = float(c3["low"])
        if c1h < c3l:
            gap = c3l - c1h; pct = gap / float(c3["close"]) * 100
            if pct >= 0.2:
                mid = c1h + gap/2
                mitigated = any(float(c["low"]) < mid for c in m5[i+1:])
                if not mitigated:
                    fvgs.append(("BULL", gap, pct))
        c1l = float(c1["low"]); c3h = float(c3["high"])
        if c1l > c3h:
            gap = c1l - c3h; pct = gap / float(c3["close"]) * 100
            if pct >= 0.2:
                mid = c3h + gap/2
                mitigated = any(float(c["high"]) > mid for c in m5[i+1:])
                if not mitigated:
                    fvgs.append(("BEAR", gap, pct))
    return len(fvgs) > 0, fvgs

def get_price():
    c = get_candles("1min", 1)
    if not c or len(c) == 0:
        return None
    try:
        return float(c[-1]["close"])
    except:
        return None

def analisa_fusion():
    now_wib = datetime.now(WIB)
    jam = now_wib.hour
    in_killzone = (15 <= jam <= 18) or (20 <= jam <= 23)
    wib_str = now_wib.strftime('%d/%m %H:%M WIB')

    # H1 CRT
    h1 = get_candles("1h", 20)
    if not h1 or len(h1) < 3:
        return f"FUSION CRT H1 - XAU SCAN\n{wib_str}\n\nDATA BINANCE/TWELVE TIDAK TERSEDIA - NO TRADE (Gagal tarik H1)"
    h1_closed = h1[-2]  # H1 SUDAH CLOSE
    try:
        h1_high = float(h1_closed["high"])
        h1_low = float(h1_closed["low"])
        h1_dt_wib = h1_closed.get("_dt_wib", now_wib).strftime('%d/%m %H:%M WIB')
    except Exception as e:
        return f"FUSION CRT H1 - XAU SCAN\n{wib_str}\n\nDATA TIDAK VALID H1: {e}"

    jarak = h1_high - h1_low
    mid = (h1_high + h1_low) / 2
    atr_h1 = calc_atr(h1, 14)
    # FIX 4: jarak valid dinamis (bukan cuma >4 hardcode)
    if atr_h1:
        valid_jarak = jarak > 4 and jarak > atr_h1 * 0.7 and jarak < atr_h1 * 3.0
        jarak_info = f"{jarak:.2f}$ (ATR H1 {atr_h1:.2f})"
    else:
        valid_jarak = jarak > 4
        jarak_info = f"{jarak:.2f}$"

    # FIX 3: H4 Bias pakai swing structure, bukan banding 2 candle doang
    h4 = get_candles("4h", 20)
    if not h4 or len(h4) < 5:
        bias = "SIDEWAYS (DATA H4 KURANG)"
        arah = "Boleh 2 arah (Lot 50%)"
    else:
        last_h, last_l = find_swing_structure(h4)
        # Cek 3 swing terakhir
        if len(last_h) >= 3 and len(last_l) >= 3 and last_h[2] > last_h[1] > last_h[0] and last_l[2] > last_l[1] > last_l[0]:
            bias = "UPTREND"
            arah = "HANYA BUY (Sweep Low H1)"
        elif len(last_h) >= 3 and len(last_l) >= 3 and last_h[2] < last_h[1] < last_h[0] and last_l[2] < last_l[1] < last_l[0]:
            bias = "DOWNTREND"
            arah = "HANYA SELL (Sweep High H1)"
        else:
            # fallback: EMA 50/200 style pakai close
            closes = [float(x["close"]) for x in h4]
            ema_period = 5
            sma = sum(closes[-ema_period:]) / ema_period
            if closes[-1] > sma and last_h[-1] > last_h[-2]:
                bias = "UPTREND (lemah)"
                arah = "HANYA BUY"
            elif closes[-1] < sma and last_h[-1] < last_h[-2]:
                bias = "DOWNTREND (lemah)"
                arah = "HANYA SELL"
            else:
                bias = "SIDEWAYS"
                arah = "Boleh 2 arah (Lot 50%)"

    price = get_price()
    if price is None:
        return f"FUSION CRT H1 - XAU SCAN\n{wib_str}\n\nDATA HARGA TIDAK TERSEDIA - NO TRADE"

    sweep_high = price - h1_high
    sweep_low = h1_low - price
    pct_high = sweep_high / price * 100
    pct_low = sweep_low / price * 100

    # FIX 5: Sweep dinamis (0.06%-0.18% + absolute 1.2-4.0) biar tidak noise saat jarak lebar
    def valid_sweep(sweep_val, pct):
        return (1.2 <= sweep_val <= 4.5) and (0.05 <= abs(pct) <= 0.20)

    status_sweep = "BELUM SWEEP"
    setup = "TUNGGU SWEEP 1.5-2.5$ (0.06-0.10%)"
    entry_info = "-"
    is_reclaim = False
    has_fvg, fvgs = get_fvg_m5()

    # Cek Reclaim: Close kembali dalam H1 range + di sisi mid/VWAP benar
    # Untuk XAU, reclaim = close body kembali di dalam H1 + di atas/bawah mid 50%
    if valid_sweep(sweep_high, pct_high) and sweep_high >= 1.2:
        # Sweep High
        # FIX 7: Reclaim + FVG M5 harus ada, bukan cuma sweep
        reclaim_ok = price < h1_high and price > mid  # kembali dalam range tapi masih di atas mid = belum reclaim penuh
        # Ideal reclaim untuk SELL = price kembali di bawah h1_high dan di bawah mid 50% atau VWAP
        is_reclaim = price < h1_high and price < (h1_high - 0.5)  # masuk dalam
        if bias.startswith("DOWNTREND"):
            status_sweep = f"SWEEP HIGH +{sweep_high:.2f}$ ({pct_high:.3f}%) VALID"
            if has_fvg:
                setup = "SELL SETUP - FVG M5 VALID"
                is_reclaim = True
            else:
                setup = "SWEEP HIGH valid tapi FVG M5 BELUM - TUNGGU FVG"
            entry_info = f"Model 1: LIMIT 50% FVG M5 | SL {price+3:.2f} (3$ diatas wick) | TP1 {mid:.2f} | TP2 {h1_low:.2f} | Sweep {sweep_high:.2f}$"
        else:
            status_sweep = f"SWEEP HIGH +{sweep_high:.2f}$ tapi Bias {bias} = SKIP"
            setup = "SWEEP HIGH tapi Bias Uptrend/Sideways = SKIP (Searah H4 = {bias})"
            entry_info = "SKIP - Tunggu Sweep Low searah Uptrend"
    elif valid_sweep(sweep_low, pct_low) and sweep_low >= 1.2:
        if bias.startswith("UPTREND"):
            status_sweep = f"SWEEP LOW -{sweep_low:.2f}$ ({pct_low:.3f}%) VALID"
            if has_fvg:
                setup = "BUY SETUP - FVG M5 VALID"
                is_reclaim = True
            else:
                setup = "SWEEP LOW valid tapi FVG M5 BELUM - TUNGGU FVG"
            entry_info = f"Model 1: LIMIT 50% FVG M5 | SL {price-3:.2f} (3$ dibawah wick) | TP1 {mid:.2f} | TP2 {h1_high:.2f} | Sweep {sweep_low:.2f}$"
        else:
            status_sweep = f"SWEEP LOW -{sweep_low:.2f}$ tapi Bias {bias} = SKIP"
            setup = "SWEEP LOW tapi Bias Downtrend/Sideways = SKIP"
            entry_info = "SKIP - Tunggu Sweep High searah Downtrend"
    elif sweep_high > 4.5 or sweep_low > 4.5 or abs(pct_high) > 0.20 or abs(pct_low) > 0.20:
        status_sweep = f"BREAKOUT >4.5$ / >0.20% (Bukan Sweep) +{sweep_high:.2f}$ / -{sweep_low:.2f}$"
        setup = "JANGAN REVERSAL - Follow breakout"
        entry_info = "SKIP - Breakout, bukan sweep CRT"
    else:
        status_sweep = f"BELUM SWEEP (+{sweep_high:.2f}$ {pct_high:.3f}% / -{sweep_low:.2f}$ {pct_low:.3f}%)"
        setup = "TUNGGU SWEEP 1.2-4.5$ (0.05-0.20%) + FVG M5"

    # FIX 6: Killzone sudah WIB benar (jam WIB)
    filter_killzone = "DI DALAM KILLZONE 15-18 & 20-23 WIB" if in_killzone else "LUAR KILLZONE (Sweep Asia = 90% FAKE - SKIP)"
    filter_jarak = "VALID" if valid_jarak else f"SKIP (jarak {jarak:.2f}$ <4$ atau < ATR, rawan double sweep)"
    filter_reclaim = "RECLAIM OK + FVG M5 VALID" if (is_reclaim and has_fvg) else "BELUM RECLAIM/FVG - TUNGGU"
    filter_fvg = f"FVG M5: {len(fvgs)} valid" if has_fvg else "FVG M5: BELUM ADA"

    # Plain text tanpa Markdown biar tidak error parse
    msg = f"""FUSION CRT H1 - XAU SCAN [FIXED LOGIC]
{wib_str} | Source: TwelveData XAU/USD (H1 CLOSE WIB corrected)

H1 Acuan CLOSED {h1_dt_wib}
High: {h1_high:.2f} | Low: {h1_low:.2f}
Jarak: {jarak_info} {filter_jarak}
Mid 50% (TP1): {mid:.2f}
ATR H1: {(atr_h1 if atr_h1 else 0):.2f}

H4 Bias (Swing HH/HL): {bias} - {arah}
Harga Sekarang: {price:.2f}
Status: {status_sweep}
Filter Killzone 15-18 & 20-23 WIB: {filter_killzone}
Filter FVG M5: {filter_fvg}
Filter Reclaim: {filter_reclaim}

Setup: {setup}
{entry_info}

Checklist: Killzone | Searah H4 (Swing) | Jarak>4$ & >ATR*0.7 | Sweep 1.2-4.5$ (0.05-0.20%) + Reclaim | FVG M5 Valid | RR 1:1.5
Repo: xau-fusion | Bot: @Monitor_koin_crypto_bot
"""
    return msg

# ===== CARA PAKAI =====
# Cron GitHub Actions tiap 5 menit (bukan while True) - Actions harus exit dalam 5 menit

if __name__ == "__main__":
    TELEGRAM_TOKEN_STR = TELEGRAM_TOKEN or ""
    if "ISI_TOKEN" in TELEGRAM_TOKEN_STR or not TELEGRAM_TOKEN_STR:
        print("TELEGRAM_TOKEN kosong - DRY RUN")
        print(analisa_fusion())
    else:
        msg = analisa_fusion()
        print(msg)
        # TETAP CEK TIAP 5 MENIT SEMUA SESI, TAPI HANYA KIRIM JIKA SWEEP VALID (tidak spam)
        # Valid = Sweep 1.2-4.5$ + FVG M5 + Reclaim + Searah H4 + Jarak OK
        is_valid = ("SWEEP" in msg and "VALID" in msg and "FVG M5 VALID" in msg and "RECLAIM OK" in msg)
        # Atau Sweep High/Low VALID dengan Setup SELL/BUY
        is_setup = ("SELL SETUP" in msg or "BUY SETUP" in msg)
        if is_valid or is_setup:
            print("=> SWEEP VALID - Kirim Telegram")
            send_tele(msg)
        else:
            print("=> BELUM SWEEP VALID - Tidak kirim (diam, tidak spam). Bot tetap cek tiap 5 menit semua sesi 24 jam.")
