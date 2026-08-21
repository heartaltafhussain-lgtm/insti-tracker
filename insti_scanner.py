#!/usr/bin/env python3
"""
INSTI TRACKER — NSE Institutional Flow Scanner  v1.0
====================================================
Kya track karta hai (sab NSE ke official public data se):
  1. BULK DEALS   (archives.nseindia.com/content/equities/bulk.csv)  — >=0.5% equity ke bade deals
  2. BLOCK DEALS  (archives.nseindia.com/content/equities/block.csv) — >₹10Cr single window trades
  3. FII/DII daily net investment (nseindia.com/api/fiidiiTradeReact)

Har deal ka client auto-classify hota hai:
  INSTITUTION — Mutual Fund, Insurance, Bank, FII/FPI, AMC, Pension, Securities, AIF...
  PROMOTER    — "PROMOTER", "HUF", "FOUNDER", "DIRECTOR", company-namesake entities
  OTHER       — baaki (individuals, LLPs, ventures...)

Output:
  insti_live_data.json   — live dashboard data (deals + fiidii + weekly/fortnightly/monthly top5)
  history/all.json       — date-wise deals + fiidii history (roz append, last 60 din)
  history/delivery.json  — date-wise NSE bhavcopy delivery data (% change compute ke liye)
  INSTI_Deals.csv        — aaj ke saare deals
  INSTI_Accumulation.csv — 7D institutional accumulation leaderboard
  INSTI_WeeklyTop5.csv     — buy/sell % change 7d vs 7d (Friday final, daily refresh)
  INSTI_FortnightlyTop5.csv— buy/sell % change 15d vs 15d (16th of month final, daily refresh)
  INSTI_MonthlyTop5.csv    — buy/sell % change MTD vs prev month (month-end final, daily refresh)
  INSTI_TriplePositive.csv — teeno periods me buying positive wale stocks

GitHub Actions: Mon-Fri 18:30 IST (NSE deals evening me update hote hain)
Password ref: 7004602
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.request

VERSION = "v1.0 Insti Tracker"
PASSWORD_REF = "7004602"

BULK_URL = "https://archives.nseindia.com/content/equities/bulk.csv"
BLOCK_URL = "https://archives.nseindia.com/content/equities/block.csv"
FIIDII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(HERE, "history", "all.json")
LIVE_FILE = os.path.join(HERE, "insti_live_data.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HDRS = {"User-Agent": UA, "Accept": "*/*", "Referer": "https://www.nseindia.com/reports/fii-dii"}

KEEP_DAYS = 60          # history me kitne din rakhen
AGG_WINDOW_DAYS = 7     # accumulation leaderboard window

# ---------------- CLIENT CLASSIFICATION ----------------
INSTI_KEYWORDS = [
    "MUTUAL FUND", "AMC", "ASSET MANAGEMENT", "INSURANCE", "LIFE INSURANCE",
    "GENERAL INSURANCE", "PENSION", "PROVIDENT FUND", " EPF ", "LIC OF INDIA", " LIC ",
    "FINANCIAL SERVICES", "CAPITAL MARKETS", "SECURITIES", "BROKING", "STOCK BROKING",
    "INVESTMENT TRUST", "INVESTMENTS", "INVESTMENT MANAGEMENT", "FUND ADVISORS",
    "AIF", "ALTERNATIVE INVESTMENT", "HEDGE FUND", "VENTURE CAPITAL", "PRIVATE EQUITY",
    "PORTFOLIO MANAGEMENT", " PMS ", "SOVEREIGN", "CENTRAL BANK",
    "FOREIGN PORTFOLIO", "FPI", "FII", " GDR ", "ADR",
    "SOCIETE GENERALE", "GOLDMAN", "MORGAN STANLEY", "NOMURA", "UBS ", "UBS AG",
    "CITIGROUP", "CITIBANK", "JPMORGAN", "JP MORGAN", "HSBC", "STANDARD CHARTERED",
    "DEUTSCHE", "BNP", "CREDIT SUISSE", "MERRILL", "BARCLAYS", "CLSA", "MACQUARIE",
    "VANGUARD", "BLACKROCK", "FIDELITY", "WELLINGTON", "CAPITAL GROUP",
    "GOVERNMENT OF SINGAPORE", "ABU DHABI", "QATAR", "NORGES", "EUROPACIFIC",
    "SMALLCAP WORLD", "ABRDN", "JANE STREET", "TOWER RESEARCH", "GRAVITON",
    "HRTI", "QE SECURITIES", "EAST BRIDGE", "COPTHALL", "CRESTA", "BNY MELLON",
    "NOMURA INDIA", "AXIS MUTUAL", "ICICI PRU", "HDFC ", "KOTAK ", "NIPPON ",
    "SBI LIFE", "SBI MUTUAL", "SBI FUND", "TATA AIA", "TATA MUTUAL",
    "MAX LIFE", "BAJAJ ALLIANZ", "ADITYA BIRLA", "CANARA ROBECO", "MIRAE",
    "FRANKLIN", "INVESCO", "JUPITER", "ALCHEMY", "MASSACHUSETTS", "ONTARIO",
    "TEMASEK", "GIC ", "GQG", "MARSHALL WACE", "MILLENNIUM", "RENAISSANCE",
    "DIMENSIONAL", "T. ROWE", "T ROWE", "NINETY ONE", "PICTET", "SCHRODER",
    "SANDS CAPITAL", "GOLDMAN SACHS", "GRANTHAM", "GMO ", "MOTILAL",
    "SEQUOIA", "TIGER GLOBAL", "SOFTBANK", "ACCEL", "LIGHTSPEED", "TRUSTPLUTUS",
    "TRADING", "WEALTH", "NUVAMA", "FINSOL", "ALPHAGREP", "GRAVITON",
]
PROMOTER_KEYWORDS = ["PROMOTER", "HUF", "FOUNDER", "DIRECTOR"]


def classify_client(client, security_name=""):
    """INSTITUTION / PROMOTER / OTHER (insti keywords pehle check — 'CAPITAL GROUP' jaise FII
    names me 'GROUP' hota hai, isliye promoter check baad me)"""
    c = (client or "").upper().strip()
    if not c:
        return "OTHER"
    if any(k in c for k in INSTI_KEYWORDS):
        return "INSTITUTION"
    if any(k in c for k in PROMOTER_KEYWORDS):
        return "PROMOTER"
    # company-namesake entity = promoter group (e.g., "RELIANCE INDUSTRIES INVESTMENTS")
    if security_name:
        first_word = re.split(r"\W+", security_name.strip().upper())[0]
        if len(first_word) >= 4 and first_word in c:
            return "PROMOTER"
    return "OTHER"


# ---------------- FETCH HELPERS ----------------
def fetch_text(url, retries=4, timeout=40):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HDRS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            log(f"    fetch retry {attempt + 1}/{retries} for {url.split('/')[-1]}: {exc}")
            time.sleep(2 + attempt * 2)
    return None


def parse_deals_csv(text, deal_type):
    rows = []
    if not text:
        return rows
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return rows
    try:
        i_date = header.index("Date")
        i_sym = header.index("Symbol")
        i_name = header.index("Security Name")
        i_client = header.index("Client Name")
        i_side = header.index("Buy/Sell")
        i_qty = header.index("Quantity Traded")
        i_price = header.index("Trade Price / Wght. Avg. Price")
    except ValueError:
        return rows
    for r in reader:
        if len(r) < 7 or not r[i_sym] or not r[i_client]:
            continue
        try:
            qty = float(r[i_qty].replace(",", ""))
            price = float(r[i_price].replace(",", ""))
        except (ValueError, IndexError):
            continue
        value_cr = round(qty * price / 1e7, 2)
        client = re.sub(r"\s+", " ", r[i_client].strip())
        rows.append({
            "date": r[i_date],
            "sym": r[i_sym].strip(),
            "name": r[i_name].strip(),
            "client": client,
            "side": r[i_side].strip().upper(),
            "qty": int(qty),
            "price": round(price, 2),
            "valueCr": value_cr,
            "type": deal_type,
            "cls": classify_client(client, r[i_name]),
        })
    return rows


def fetch_fiidii():
    text = fetch_text(FIIDII_URL)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    out = {}
    for row in data:
        cat = str(row.get("category", ""))
        try:
            buy = float(row.get("buyValue"))
            sell = float(row.get("sellValue"))
            net = float(row.get("netValue"))
        except (TypeError, ValueError):
            continue
        key = "FII" if "FII" in cat.upper() else ("DII" if "DII" in cat.upper() else None)
        if key:
            out[key] = {"buy": round(buy, 2), "sell": round(sell, 2), "net": round(net, 2)}
    return out if out else None


# ---------------- UNIVERSE MAPS ----------------
def load_name_map():
    path = os.path.join(HERE, "nse_symbols.csv")
    m = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                sym = (row.get("Symbol") or "").strip()
                name = (row.get("Name") or "").strip()
                if sym:
                    m[sym] = name
    return m


def load_sector_map():
    path = os.path.join(HERE, "nifty500_universe.csv")
    m = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                sym = (row.get("symbol") or row.get("Symbol") or "").strip()
                ind = (row.get("industry") or row.get("Industry") or "").strip()
                if sym:
                    m[sym] = ind
    return m


# ---------------- HISTORY ----------------
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {"updated": "", "dates": [], "scans": {}}


def save_history(hist):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
        json.dump(hist, fh, indent=1, ensure_ascii=False)


def append_history(hist, scan_date, deals, fiidii):
    scans = hist.setdefault("scans", {})
    scans[scan_date] = {"date": scan_date, "deals": deals, "fiidii": fiidii}
    dates = sorted(scans.keys(), reverse=True)
    if len(dates) > KEEP_DAYS:
        for old in dates[KEEP_DAYS:]:
            scans.pop(old, None)
        dates = dates[:KEEP_DAYS]
    hist["dates"] = dates
    hist["updated"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")


# ---------------- AGGREGATION ----------------
def aggregate(deals_by_date, window=AGG_WINDOW_DAYS):
    """Per-symbol institutional accumulation over window (dates desc sorted)."""
    dates = sorted(deals_by_date.keys(), reverse=True)[:window]
    agg = {}
    for d in dates:
        for dl in deals_by_date[d]:
            if dl["cls"] != "INSTITUTION":
                continue
            a = agg.setdefault(dl["sym"], {
                "sym": dl["sym"], "name": dl["name"], "sector": dl.get("sector", ""),
                "buyCr": 0.0, "sellCr": 0.0, "buys": 0, "sells": 0,
                "clients": set(), "days": set(),
            })
            if dl["side"] == "BUY":
                a["buyCr"] += dl["valueCr"]
                a["buys"] += 1
            else:
                a["sellCr"] += dl["valueCr"]
                a["sells"] += 1
            a["clients"].add(dl["client"])
            a["days"].add(d)
    rows = []
    for a in agg.values():
        net = round(a["buyCr"] - a["sellCr"], 2)
        status = "ACCUMULATING" if net > 0 else ("DISTRIBUTING" if net < 0 else "FLAT")
        rows.append({
            "sym": a["sym"], "name": a["name"], "sector": a["sector"],
            "buyCr": round(a["buyCr"], 2), "sellCr": round(a["sellCr"], 2),
            "netCr": net, "buys": a["buys"], "sells": a["sells"],
            "nInsti": len(a["clients"]), "nDays": len(a["days"]),
            "status": status,
        })
    rows.sort(key=lambda r: (-abs(r["netCr"]), -(r["buyCr"] + r["sellCr"])))
    return rows


def sector_flow(deals_by_date, window=AGG_WINDOW_DAYS):
    dates = sorted(deals_by_date.keys(), reverse=True)[:window]
    flow = {}
    for d in dates:
        for dl in deals_by_date[d]:
            if dl["cls"] != "INSTITUTION":
                continue
            f = flow.setdefault(dl.get("sector") or "UNMAPPED", {"buyCr": 0.0, "sellCr": 0.0, "deals": 0})
            if dl["side"] == "BUY":
                f["buyCr"] += dl["valueCr"]
            else:
                f["sellCr"] += dl["valueCr"]
            f["deals"] += 1
    rows = [{"sector": k, "buyCr": round(v["buyCr"], 2), "sellCr": round(v["sellCr"], 2),
             "netCr": round(v["buyCr"] - v["sellCr"], 2), "deals": v["deals"]}
            for k, v in flow.items()]
    rows.sort(key=lambda r: -abs(r["netCr"]))
    return rows


def log(msg):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}")


# ================= DELIVERY-BASED INSTITUTIONAL BUY/SELL % CHANGE =================
# Institutional buying/selling ka daily public proxy = NSE bhavcopy:
#   BUY  proxy = DELIV_QTY (institutions delivery-based buying karte hain)
#   SELL proxy = TTL_TRD_QNTY - DELIV_QTY (intraday churn = selling pressure)
# Periods (user spec):
#   WEEKLY      = pichhle 7 calendar din (cur) vs usse pehle 7 din (prev)  — Friday final
#   FORTNIGHTLY = pichhle 15 din vs usse pehle 15 din                       — 16th of month final
#   MONTHLY     = is month-to-date vs pichhla poora calendar month          — month-end final
# Formula: % diff = (curBuy - prevBuy) / prevBuy * 100  (buying AUR selling dono ka)
# Universe = Nifty 500. Teeno daily recompute hote hain. + TRIPLE POSITIVE list
# (teeno periods me buying % positive wale stocks).

BHAVDATA_URL = "https://archives.nseindia.com/products/content/sec_bhavdata_full_{}.csv"
DELIVERY_FILE = os.path.join(HERE, "history", "delivery.json")
KEEP_DELIVERY_DAYS = 75      # history me kitne din ka delivery data rakhen
MIN_PREV_QTY = 50000         # prev period ki minimum buy/sell qty (noise filter)
MIN_CUR_DAYS = 3             # current period me minimum trading din
BOOTSTRAP_DAYS = 55          # pehli baar 55 calendar din pichhe (pichhla poora month mile)
TRIPLE_CAP = 60              # triple positive list ki max rows


def fetch_bhav(d):
    """Ek din ka bhavcopy fetch karo -> {SYM: [delivQty, delivPer, close, tradedQty]} (sirf EQ)."""
    ddmmyyyy = d.strftime("%d%m%Y")
    text = fetch_text(BHAVDATA_URL.format(ddmmyyyy))
    if not text:
        return None
    lines = text.splitlines()
    if len(lines) < 2:
        return None
    header = [h.strip() for h in lines[0].split(",")]
    try:
        i_sym = header.index("SYMBOL")
        i_ser = header.index("SERIES")
        i_dq = header.index("DELIV_QTY")
        i_dp = header.index("DELIV_PER")
        i_cl = header.index("CLOSE_PRICE")
        i_tq = header.index("TTL_TRD_QNTY")
    except ValueError:
        return None
    out = {}
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) <= max(i_sym, i_ser, i_dq, i_dp, i_cl, i_tq):
            continue
        if parts[i_ser].strip() != "EQ":
            continue
        try:
            dq = float(parts[i_dq].strip())
            dp = float(parts[i_dp].strip())
            cl = float(parts[i_cl].strip())
            tq = float(parts[i_tq].strip())
        except ValueError:
            continue
        out[parts[i_sym].strip()] = [dq, dp, cl, tq]
    return out or None


def load_delivery():
    dv = {"updated": "", "days": {}}
    if os.path.exists(DELIVERY_FILE):
        try:
            with open(DELIVERY_FILE, encoding="utf-8") as fh:
                dv = json.load(fh)
            # v1 format ([dq, dp, cl]) hai to wipe karo — v2 ([dq, dp, cl, tq]) chahiye
            if dv.get("days"):
                first_day = next(iter(dv["days"].values()))
                sample = next(iter(first_day.values())) if first_day else None
                if sample and len(sample) < 4:
                    log("  [MIGRATE] purana delivery format mila — re-bootstrap")
                    dv = {"updated": "", "days": {}}
        except Exception:
            dv = {"updated": "", "days": {}}
    return dv


def save_delivery(dv):
    os.makedirs(os.path.dirname(DELIVERY_FILE), exist_ok=True)
    with open(DELIVERY_FILE, "w", encoding="utf-8") as fh:
        json.dump(dv, fh, indent=1)


def bootstrap_delivery(dv):
    """Pehli baar pichhle BOOTSTRAP_DAYS calendar din ka delivery data bharo."""
    today = dt.date.today()
    got = 0
    for back in range(0, BOOTSTRAP_DAYS):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        key = d.isoformat()
        if key in dv["days"]:
            continue
        data = fetch_bhav(d)
        if data:
            dv["days"][key] = data
            got += 1
        time.sleep(0.4)
    keys = sorted(dv["days"].keys())
    if len(keys) > KEEP_DELIVERY_DAYS:
        for k in keys[:-KEEP_DELIVERY_DAYS]:
            dv["days"].pop(k, None)
    dv["updated"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
    save_delivery(dv)
    return got


def _deal_date_iso(s):
    """'20-AUG-2026' -> '2026-08-20'"""
    try:
        return dt.datetime.strptime(s.strip(), "%d-%b-%Y").date().isoformat()
    except (ValueError, AttributeError):
        return None


def _insti_net_range(deals_by_date, ref_iso, cur_iso):
    """Institutions ka net ₹Cr per symbol, ref ke baad (exclusive) se cur tak."""
    net = {}
    for d, deals in deals_by_date.items():
        diso = _deal_date_iso(d)
        if not diso or diso <= ref_iso or diso > cur_iso:
            continue
        for dl in deals:
            if dl["cls"] != "INSTITUTION":
                continue
            n = net.setdefault(dl["sym"], {"netCr": 0.0, "deals": 0})
            n["netCr"] += dl["valueCr"] if dl["side"] == "BUY" else -dl["valueCr"]
            n["deals"] += 1
    for v in net.values():
        v["netCr"] = round(v["netCr"], 2)
    return net


def _period_sums(dv, sym, day_list):
    """Period ke days me sym ki total BUY qty (delivery) aur SELL qty (traded-delivery)."""
    buy = sell = 0.0
    for d in day_list:
        row = (dv["days"].get(d) or {}).get(sym)
        if row:
            dq, _dp, _cl, tq = row
            buy += dq
            sell += max(tq - dq, 0.0)
    return buy, sell


def _pct(cur_sum, prev_sum):
    if prev_sum < MIN_PREV_QTY:
        return None
    return round((cur_sum - prev_sum) / prev_sum * 100.0, 1)


def _dlist(dates, lo_excl, hi_incl):
    return [d for d in dates if lo_excl < d <= hi_incl]


def _is_friday(d):
    return dt.date.fromisoformat(d).weekday() == 4


def _is_month_end(d):
    dd = dt.date.fromisoformat(d)
    return (dd + dt.timedelta(days=4)).month != dd.month


def _is_fortnight_final(dates):
    """Har month ki 16th (ya 16th ke baad pehla trading din) = FORTNIGHTLY FINAL."""
    if not dates:
        return False
    cur = dt.date.fromisoformat(dates[-1])
    if cur.day < 16:
        return False
    if len(dates) < 2:
        return True
    prev = dt.date.fromisoformat(dates[-2])
    return prev.day < 16 or prev.month != cur.month


def _period_row(sym, name, sector, buyPct, sellPct, curBuy, prevBuy, curSell, prevSell,
                curDays, close, ov):
    curT = curBuy + curSell
    prevT = prevBuy + prevSell
    return {
        "sym": sym, "name": name, "sector": sector,
        "buyPct": buyPct, "sellPct": sellPct,
        "curBuy": int(round(curBuy)), "prevBuy": int(round(prevBuy)),
        "curSell": int(round(curSell)), "prevSell": int(round(prevSell)),
        "dpCur": round(curBuy / curT * 100, 1) if curT > 0 else 0.0,
        "dpPrev": round(prevBuy / prevT * 100, 1) if prevT > 0 else 0.0,
        "curDays": curDays, "close": round(close, 2),
        "instiNetCr": ov.get("netCr", 0.0), "instiDeals": ov.get("deals", 0),
    }


def compute_delivery_sections(dv, universe, name_map, sector_map, deals_by_date):
    dates = sorted(dv["days"].keys())
    if not dates:
        return None, None, None, None
    cur = dates[-1]
    cur_d = dt.date.fromisoformat(cur)
    iso = lambda d: d.isoformat()  # noqa: E731

    # ---- period windows (calendar-day based) ----
    w_cur = _dlist(dates, iso(cur_d - dt.timedelta(days=7)), cur)
    w_prev = _dlist(dates, iso(cur_d - dt.timedelta(days=14)), iso(cur_d - dt.timedelta(days=7)))
    f_cur = _dlist(dates, iso(cur_d - dt.timedelta(days=15)), cur)
    f_prev = _dlist(dates, iso(cur_d - dt.timedelta(days=30)), iso(cur_d - dt.timedelta(days=15)))
    m_cur = [d for d in dates if dt.date.fromisoformat(d).year == cur_d.year
             and dt.date.fromisoformat(d).month == cur_d.month]
    pm = (cur_d.replace(day=1) - dt.timedelta(days=1))
    m_prev = [d for d in dates if dt.date.fromisoformat(d).year == pm.year
              and dt.date.fromisoformat(d).month == pm.month]

    # ---- insti deals overlay per period ----
    ov_w = _insti_net_range(deals_by_date, w_prev[-1] if w_prev else cur, cur)
    ov_f = _insti_net_range(deals_by_date, f_prev[-1] if f_prev else cur, cur)
    ov_m = _insti_net_range(deals_by_date, m_prev[-1] if m_prev else cur, cur)

    w_rows, f_rows, m_rows = [], [], []
    triple = []
    nw = nf = nm = 0
    for sym in universe:
        wb, ws = _period_sums(dv, sym, w_cur)
        wbp, wsp = _period_sums(dv, sym, w_prev)
        fb, fs = _period_sums(dv, sym, f_cur)
        fbp, fsp = _period_sums(dv, sym, f_prev)
        mb, ms = _period_sums(dv, sym, m_cur)
        mbp, msp = _period_sums(dv, sym, m_prev)
        close = 0.0
        lastrow = (dv["days"].get(cur) or {}).get(sym)
        if lastrow:
            close = lastrow[2]
        name = name_map.get(sym) or ""
        sector = sector_map.get(sym) or ""

        wBuyPct = _pct(wb, wbp) if len(w_cur) >= MIN_CUR_DAYS and len(w_prev) >= MIN_CUR_DAYS else None
        fBuyPct = _pct(fb, fbp) if len(f_cur) >= MIN_CUR_DAYS and len(f_prev) >= MIN_CUR_DAYS else None
        mBuyPct = _pct(mb, mbp) if len(m_cur) >= MIN_CUR_DAYS and len(m_prev) >= MIN_CUR_DAYS else None
        wSellPct = _pct(ws, wsp) if wBuyPct is not None else None
        fSellPct = _pct(fs, fsp) if fBuyPct is not None else None
        mSellPct = _pct(ms, msp) if mBuyPct is not None else None

        if wBuyPct is not None:
            nw += 1
            w_rows.append(_period_row(sym, name, sector, wBuyPct, wSellPct, wb, wbp, ws, wsp,
                                      len(w_cur), close, ov_w.get(sym) or {}))
        if fBuyPct is not None:
            nf += 1
            f_rows.append(_period_row(sym, name, sector, fBuyPct, fSellPct, fb, fbp, fs, fsp,
                                      len(f_cur), close, ov_f.get(sym) or {}))
        if mBuyPct is not None:
            nm += 1
            m_rows.append(_period_row(sym, name, sector, mBuyPct, mSellPct, mb, mbp, ms, msp,
                                      len(m_cur), close, ov_m.get(sym) or {}))
        # ---- TRIPLE POSITIVE: teeno periods me buying % positive ----
        if (wBuyPct is not None and fBuyPct is not None and mBuyPct is not None
                and wBuyPct > 0 and fBuyPct > 0 and mBuyPct > 0):
            triple.append({
                "sym": sym, "name": name, "sector": sector,
                "wBuyPct": wBuyPct, "fBuyPct": fBuyPct, "mBuyPct": mBuyPct,
                "score": round(wBuyPct + fBuyPct + mBuyPct, 1),
                "close": round(close, 2),
                "instiNetCr": ov_m.get(sym, {}).get("netCr", 0.0),
                "instiDeals": ov_m.get(sym, {}).get("deals", 0),
            })

    w_rows.sort(key=lambda r: -r["buyPct"])
    f_rows.sort(key=lambda r: -r["buyPct"])
    m_rows.sort(key=lambda r: -r["buyPct"])
    triple.sort(key=lambda r: -r["score"])

    weekly = {
        "curDate": cur, "refDate": w_prev[-1] if w_prev else None,
        "final": _is_friday(cur),
        "status": "FINAL WEEKLY ✅ (Friday close)" if _is_friday(cur) else "WEEK IN PROGRESS — daily refresh, Friday ko final",
        "coverage": nw, "rows": w_rows[:5],
    }
    fortnightly = {
        "curDate": cur, "refDate": f_prev[-1] if f_prev else None,
        "final": _is_fortnight_final(dates),
        "status": "FINAL FORTNIGHTLY ✅ (16th of month)" if _is_fortnight_final(dates) else "FORTNIGHT IN PROGRESS — daily refresh, har month ki 16th ko final",
        "coverage": nf, "rows": f_rows[:5],
    }
    monthly = {
        "curDate": cur, "anchorDate": m_prev[-1] if m_prev else None,
        "final": _is_month_end(cur),
        "status": "FINAL MONTHLY ✅ (month-end close)" if _is_month_end(cur) else "MONTH IN PROGRESS — daily refresh, month-end ko final",
        "coverage": nm, "rows": m_rows[:5],
    }
    triple_sec = {
        "count": len(triple), "curDate": cur,
        "rows": triple[:TRIPLE_CAP],
    }
    return weekly, fortnightly, monthly, triple_sec
# ---------------- MAIN ----------------
def scan():
    log("=" * 72)
    log("INSTI TRACKER SCAN — NSE bulk/block deals + FII/DII")
    log("=" * 72)

    name_map = load_name_map()
    sector_map = load_sector_map()
    log(f"name map: {len(name_map)} symbols | sector map: {len(sector_map)} symbols")

    bulk_text = fetch_text(BULK_URL)
    block_text = fetch_text(BLOCK_URL)
    fiidii = fetch_fiidii()
    if bulk_text is None and block_text is None:
        log("[FATAL] NSE deals CSV fetch fail — exit.")
        sys.exit(1)

    bulk_deals = parse_deals_csv(bulk_text, "BULK")
    block_deals = parse_deals_csv(block_text, "BLOCK")
    deals = bulk_deals + block_deals
    log(f"bulk deals: {len(bulk_deals)} | block deals: {len(block_deals)} | fiidii: {fiidii}")

    if not deals:
        log("  No deals parsed — kuch galat ho sakta hai, check CSV format.")
    else:
        scan_date = deals[0]["date"]
        log(f"  deals date: {scan_date}")

    # enrich name + sector
    for dl in deals:
        dl["name"] = name_map.get(dl["sym"]) or dl["name"]
        dl["sector"] = sector_map.get(dl["sym"]) or ""

    # history load + append
    hist = load_history()
    fiidii_hist_prev = {}
    if hist["scans"]:
        last = hist["scans"].get(sorted(hist["scans"].keys(), reverse=True)[0])
        if last:
            fiidii_hist_prev = last.get("fiidii") or {}
    if deals:
        append_history(hist, scan_date, deals, fiidii if fiidii else fiidii_hist_prev)
        save_history(hist)
        log(f"  history saved: {len(hist['dates'])} din")

    # ---- DELIVERY momentum (weekly + monthly top5) ----
    universe = sorted(sector_map.keys())
    if not universe:
        log("  [WARN] nifty500 universe khali — delivery sections skip")
    dv = load_delivery()
    got = bootstrap_delivery(dv)
    log(f"  delivery history: {len(dv['days'])} din ({got} naye fetch)")
    weekly, fortnightly, monthly, triple = compute_delivery_sections(
        dv, universe, name_map, sector_map,
        {d: (hist["scans"][d].get("deals") or []) for d in hist["dates"]})

    # FII/DII series (accumulated)
    fii_series = []
    for d in hist["dates"]:
        f = (hist["scans"].get(d) or {}).get("fiidii") or {}
        if f.get("FII") or f.get("DII"):
            fii_series.append({
                "date": d,
                "fiiNet": (f.get("FII") or {}).get("net"),
                "fiiBuy": (f.get("FII") or {}).get("buy"),
                "fiiSell": (f.get("FII") or {}).get("sell"),
                "diiNet": (f.get("DII") or {}).get("net"),
                "diiBuy": (f.get("DII") or {}).get("buy"),
                "diiSell": (f.get("DII") or {}).get("sell"),
            })
    fii_series.reverse()  # oldest -> newest

    deals_by_date = {d: (hist["scans"][d].get("deals") or []) for d in hist["dates"]}
    accum = aggregate(deals_by_date)
    sflow = sector_flow(deals_by_date)

    n_insti = sum(1 for dl in deals if dl["cls"] == "INSTITUTION")
    payload = {
        "date": hist["dates"][0] if hist["dates"] else "?",
        "timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "passwordRef": PASSWORD_REF,
        "version": VERSION,
        "scanStats": {
            "bulkDeals": len(bulk_deals),
            "blockDeals": len(block_deals),
            "instiDeals": n_insti,
            "instiSymbols": len(set(dl["sym"] for dl in deals if dl["cls"] == "INSTITUTION")),
            "historyDays": len(hist["dates"]),
        },
        "fiidiiToday": fiidii,
        "fiidiiSeries": fii_series,
        "dealsToday": deals,
        "accumulation": accum,
        "sectorFlow": sflow,
        "weeklyTop5": weekly,
        "fortnightlyTop5": fortnightly,
        "monthlyTop5": monthly,
        "triplePositive": triple,
        "dates": hist["dates"],
    }

    with open(LIVE_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
    log(f"  wrote insti_live_data.json ({os.path.getsize(LIVE_FILE) / 1024:.0f} KB)")

    # CSV exports
    if deals:
        with open(os.path.join(HERE, "INSTI_Deals.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Date", "Symbol", "Company", "Client", "Class", "Side", "Qty", "Price", "ValueCr", "Type", "Sector"])
            for dl in deals:
                w.writerow([dl["date"], dl["sym"], dl["name"], dl["client"], dl["cls"],
                            dl["side"], dl["qty"], dl["price"], dl["valueCr"], dl["type"], dl["sector"]])
    with open(os.path.join(HERE, "INSTI_Accumulation.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Symbol", "Company", "Sector", "InstiBuyCr", "InstiSellCr", "NetCr",
                    "Buys", "Sells", "Institutions", "ActiveDays", "Status"])
        for a in accum:
            w.writerow([a["sym"], a["name"], a["sector"], a["buyCr"], a["sellCr"], a["netCr"],
                        a["buys"], a["sells"], a["nInsti"], a["nDays"], a["status"]])
    log("  wrote INSTI_Deals.csv + INSTI_Accumulation.csv")

    # Weekly + Fortnightly + Monthly % change CSVs + Triple Positive CSV
    for name, sec, refkey in (("INSTI_WeeklyTop5.csv", weekly, "refDate"),
                              ("INSTI_FortnightlyTop5.csv", fortnightly, "refDate"),
                              ("INSTI_MonthlyTop5.csv", monthly, "anchorDate")):
        with open(os.path.join(HERE, name), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Rank", "Symbol", "Company", "Sector", "BuyQtyCur", "BuyQtyPrev",
                        "BuyPctChg", "SellQtyCur", "SellQtyPrev", "SellPctChg",
                        "DelivPerCur", "DelivPerPrev", "Close", "InstiNetCr", "InstiDeals",
                        "RefDate", "Status"])
            if sec:
                for i, r in enumerate(sec.get("rows") or [], 1):
                    w.writerow([i, r["sym"], r["name"], r["sector"], r["curBuy"], r["prevBuy"],
                                r["buyPct"], r["curSell"], r["prevSell"], r["sellPct"],
                                r["dpCur"], r["dpPrev"], r["close"],
                                r["instiNetCr"], r["instiDeals"], sec.get(refkey), sec.get("status")])
    with open(os.path.join(HERE, "INSTI_TriplePositive.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Rank", "Symbol", "Company", "Sector", "WeeklyBuyPct", "FortnightlyBuyPct",
                    "MonthlyBuyPct", "CombinedScore", "Close", "InstiNetCr", "InstiDeals"])
        if triple:
            for i, r in enumerate(triple.get("rows") or [], 1):
                w.writerow([i, r["sym"], r["name"], r["sector"], r["wBuyPct"], r["fBuyPct"],
                            r["mBuyPct"], r["score"], r["close"], r["instiNetCr"], r["instiDeals"]])
    log("  wrote INSTI_WeeklyTop5.csv + INSTI_FortnightlyTop5.csv + INSTI_MonthlyTop5.csv + INSTI_TriplePositive.csv")

    # summary log
    log("-" * 72)
    top = accum[:10]
    log(f"  TOP INSTITUTIONAL ACCUMULATION ({AGG_WINDOW_DAYS}D): {len(accum)} symbols")
    for a in top:
        log(f"    {a['sym']:<12} net ₹{a['netCr']:>8.2f}Cr  buy{a['buyCr']:>7.2f}/sell{a['sellCr']:>7.2f} "
            f"deals {a['buys'] + a['sells']:>2}  insti {a['nInsti']}  days {a['nDays']}  [{a['status']}]")
    if weekly:
        log("-" * 72)
        log(f"  WEEKLY TOP5 (buy% = (cur7d buy - prev7d buy)/prev7d buy | {weekly['curDate']} vs {weekly['refDate']} | "
            f"coverage {weekly['coverage']}/500) [{weekly['status']}]")
        for i, r in enumerate(weekly["rows"], 1):
            log(f"    {i}. {r['sym']:<12} BUY {r['buyPct']:>+8.1f}%  SELL {r['sellPct']:>+8.1f}%  "
                f"instiNet ₹{r['instiNetCr']}Cr  close ₹{r['close']}")
    if fortnightly:
        log("-" * 72)
        log(f"  FORTNIGHTLY TOP5 (buy% = (cur15d buy - prev15d buy)/prev15d buy | {fortnightly['curDate']} vs {fortnightly['refDate']} | "
            f"coverage {fortnightly['coverage']}/500) [{fortnightly['status']}]")
        for i, r in enumerate(fortnightly["rows"], 1):
            log(f"    {i}. {r['sym']:<12} BUY {r['buyPct']:>+8.1f}%  SELL {r['sellPct']:>+8.1f}%  "
                f"instiNet ₹{r['instiNetCr']}Cr  close ₹{r['close']}")
    if monthly:
        log("-" * 72)
        log(f"  MONTHLY TOP5 (buy% = (MTD buy - prev month buy)/prev month buy | {monthly['curDate']} vs {monthly['anchorDate']} | "
            f"coverage {monthly['coverage']}/500) [{monthly['status']}]")
        for i, r in enumerate(monthly["rows"], 1):
            log(f"    {i}. {r['sym']:<12} BUY {r['buyPct']:>+8.1f}%  SELL {r['sellPct']:>+8.1f}%  "
                f"instiNet ₹{r['instiNetCr']}Cr  close ₹{r['close']}")
    if triple:
        log("-" * 72)
        log(f"  🌟 TRIPLE POSITIVE (weekly+fortnightly+monthly teeno buying % > 0): {triple['count']} stocks")
        for i, r in enumerate(triple["rows"][:10], 1):
            log(f"    {i}. {r['sym']:<12} W +{r['wBuyPct']}%  F +{r['fBuyPct']}%  M +{r['mBuyPct']}%  "
                f"score {r['score']}  close ₹{r['close']}")
    log("=" * 72)
    return payload


if __name__ == "__main__":
    try:
        scan()
    except Exception as exc:  # noqa: BLE001
        log(f"[FATAL] {exc}")
        raise
