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
  INSTI_WeeklyTop5.csv     — Nifty 500 delivery buying % change (Friday final, daily refresh)
  INSTI_FortnightlyTop5.csv— Nifty 500 delivery buying % change (16th of month final, daily refresh)
  INSTI_MonthlyTop5.csv    — Nifty 500 delivery buying % change (month-end final, daily refresh)

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


# ================= DELIVERY-BASED BUYING MOMENTUM (NSE bhavcopy) =================
# Institutional buying ka sabse solid daily public proxy = DELIVERY data
# (NSE sec_bhavdata_full CSV me har symbol ka DELIV_QTY + DELIV_PER hota hai).
# Weekly top5     = 7 din pehle ke trading din se delivery qty ka % change (Fri final)
# Fortnightly top5= 15 din pehle ke trading din se % change (har month ki 16th ko final)
# Monthly top5    = pichhle month-end anchor se % change (month-end final)
# Teeno daily recompute hote hain, universe = Nifty 500 (500 stocks).

BHAVDATA_URL = "https://archives.nseindia.com/products/content/sec_bhavdata_full_{}.csv"
DELIVERY_FILE = os.path.join(HERE, "history", "delivery.json")
KEEP_DELIVERY_DAYS = 75      # history me kitne din ka delivery data rakhen
MIN_DELIV_QTY = 50000        # ref din pe minimum delivery qty (noise filter)
MIN_DELIV_PER = 5.0          # ref din pe minimum delivery %
BOOTSTRAP_DAYS = 24          # pehli baar kitne calendar din pichhe jakar data bharo (>=15 din ka fortnightly ref mile)


def fetch_bhav(d):
    """Ek din ka bhavcopy fetch karo -> {SYM: [deliv_qty, deliv_per, close]} (sirf EQ)."""
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
    except ValueError:
        return None
    out = {}
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) <= max(i_sym, i_ser, i_dq, i_dp, i_cl):
            continue
        if parts[i_ser].strip() != "EQ":
            continue
        try:
            dq = float(parts[i_dq].strip())
            dp = float(parts[i_dp].strip())
            cl = float(parts[i_cl].strip())
        except ValueError:
            continue
        out[parts[i_sym].strip()] = [dq, dp, cl]
    return out or None


def load_delivery():
    if os.path.exists(DELIVERY_FILE):
        try:
            with open(DELIVERY_FILE, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {"updated": "", "days": {}}


def save_delivery(dv):
    os.makedirs(os.path.dirname(DELIVERY_FILE), exist_ok=True)
    with open(DELIVERY_FILE, "w", encoding="utf-8") as fh:
        json.dump(dv, fh, indent=1)


def bootstrap_delivery(dv):
    """Pehli baar pichhle ~2 hafte + pichhle month-end ka delivery data bharo."""
    today = dt.date.today()
    got = 0
    # 1) pichhle BOOTSTRAP_DAYS calendar din (Mon-Fri)
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
            log(f"    delivery {key}: {len(data)} EQ rows")
        else:
            log(f"    delivery {key}: NA (holiday/future)")
        time.sleep(0.4)
    # 2) pichhle 2 month-end anchors (monthly % change ke liye)
    for m_off in (1, 2):
        first = today.replace(day=1)
        anchor = (first - dt.timedelta(days=1)) if m_off == 1 else (
            first.replace(day=1) - dt.timedelta(days=1)).replace(day=1) - dt.timedelta(days=1)
        while anchor.weekday() >= 5:
            anchor -= dt.timedelta(days=1)
        key = anchor.isoformat()
        if key in dv["days"]:
            continue
        data = fetch_bhav(anchor)
        if data:
            dv["days"][key] = data
            got += 1
            log(f"    delivery {key} (month anchor): {len(data)} EQ rows")
        time.sleep(0.4)
    # 3) trim
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


def build_delivery_top5(cur, cur_map, ref, ref_map, universe, name_map, sector_map, insti_net):
    rows = []
    if not ref or not ref_map:
        return rows, 0
    matched = 0
    for sym in universe:
        c = cur_map.get(sym)
        r = ref_map.get(sym)
        if not c or not r:
            continue
        dqc, dpc, clc = c
        dqr, dpr, _ = r
        if dqr < MIN_DELIV_QTY or dpr < MIN_DELIV_PER or dqc <= 0:
            continue
        matched += 1
        buy_pct = (dqc - dqr) / dqr * 100.0
        dp_chg = dpc - dpr
        ov = insti_net.get(sym) or {}
        rows.append({
            "sym": sym,
            "name": name_map.get(sym) or "",
            "sector": sector_map.get(sym) or "",
            "dqCur": int(round(dqc)), "dqRef": int(round(dqr)),
            "dpCur": round(dpc, 1), "dpRef": round(dpr, 1),
            "buyPct": round(buy_pct, 1),
            "dpChg": round(dp_chg, 1),
            "close": round(clc, 2),
            "instiNetCr": ov.get("netCr", 0.0),
            "instiDeals": ov.get("deals", 0),
        })
    rows.sort(key=lambda r: -r["buyPct"])
    return rows[:5], matched


def _is_friday(d):
    return dt.date.fromisoformat(d).weekday() == 4


def _is_month_end(d):
    dd = dt.date.fromisoformat(d)
    nxt = dd + dt.timedelta(days=4)
    return nxt.month != dd.month


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
    # 16th pehla trading din hai (prev 16th se pehle) ya month ka pehla scan hi 16th+ hai
    return prev.day < 16 or prev.month != cur.month


def compute_delivery_sections(dv, universe, name_map, sector_map, deals_by_date):
    dates = sorted(dv["days"].keys())
    if not dates:
        return None, None, None
    cur = dates[-1]
    cur_d = dt.date.fromisoformat(cur)
    cur_map = dv["days"][cur]

    ref_w = None
    for d in reversed(dates):
        if (cur_d - dt.date.fromisoformat(d)).days >= 7:
            ref_w = d
            break
    ref_f = None
    for d in reversed(dates):
        if (cur_d - dt.date.fromisoformat(d)).days >= 15:
            ref_f = d
            break
    ref_m = None
    for d in reversed(dates):
        dd = dt.date.fromisoformat(d)
        if dd.year != cur_d.year or dd.month != cur_d.month:
            ref_m = d
            break

    inet_w = _insti_net_range(deals_by_date, ref_w or cur, cur)
    inet_f = _insti_net_range(deals_by_date, ref_f or cur, cur)
    inet_m = _insti_net_range(deals_by_date, ref_m or cur, cur)

    w_rows, w_match = build_delivery_top5(cur, cur_map, ref_w, dv["days"].get(ref_w or ""),
                                          universe, name_map, sector_map, inet_w)
    f_rows, f_match = build_delivery_top5(cur, cur_map, ref_f, dv["days"].get(ref_f or ""),
                                          universe, name_map, sector_map, inet_f)
    m_rows, m_match = build_delivery_top5(cur, cur_map, ref_m, dv["days"].get(ref_m or ""),
                                          universe, name_map, sector_map, inet_m)

    weekly = {
        "curDate": cur, "refDate": ref_w,
        "final": _is_friday(cur),
        "status": "FINAL WEEKLY ✅ (Friday close)" if _is_friday(cur) else "WEEK IN PROGRESS — daily refresh, Friday ko final",
        "coverage": w_match,
        "rows": w_rows,
    }
    fortnightly = {
        "curDate": cur, "refDate": ref_f,
        "final": _is_fortnight_final(dates),
        "status": "FINAL FORTNIGHTLY ✅ (16th of month)" if _is_fortnight_final(dates) else "FORTNIGHT IN PROGRESS — daily refresh, har month ki 16th ko final",
        "coverage": f_match,
        "rows": f_rows,
    }
    monthly = {
        "curDate": cur, "anchorDate": ref_m,
        "final": _is_month_end(cur),
        "status": "FINAL MONTHLY ✅ (month-end close)" if _is_month_end(cur) else "MONTH IN PROGRESS — daily refresh, month-end ko final",
        "coverage": m_match,
        "rows": m_rows,
    }
    return weekly, fortnightly, monthly


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
    weekly, fortnightly, monthly = compute_delivery_sections(dv, universe, name_map, sector_map,
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

    # Weekly + Fortnightly + Monthly delivery momentum CSVs
    for name, sec, refkey in (("INSTI_WeeklyTop5.csv", weekly, "refDate"),
                              ("INSTI_FortnightlyTop5.csv", fortnightly, "refDate"),
                              ("INSTI_MonthlyTop5.csv", monthly, "anchorDate")):
        with open(os.path.join(HERE, name), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Rank", "Symbol", "Company", "Sector", "DelivQtyNow", "DelivQtyRef",
                        "BuyPctChg", "DelivPerNow", "DelivPerRef", "DelivPerChg",
                        "CloseNow", "InstiNetCr", "InstiDeals", "RefDate", "Status"])
            if sec:
                for i, r in enumerate(sec.get("rows") or [], 1):
                    w.writerow([i, r["sym"], r["name"], r["sector"], r["dqCur"], r["dqRef"],
                                r["buyPct"], r["dpCur"], r["dpRef"], r["dpChg"], r["close"],
                                r["instiNetCr"], r["instiDeals"], sec.get(refkey), sec.get("status")])
    log("  wrote INSTI_WeeklyTop5.csv + INSTI_FortnightlyTop5.csv + INSTI_MonthlyTop5.csv")

    # summary log
    log("-" * 72)
    top = accum[:10]
    log(f"  TOP INSTITUTIONAL ACCUMULATION ({AGG_WINDOW_DAYS}D): {len(accum)} symbols")
    for a in top:
        log(f"    {a['sym']:<12} net ₹{a['netCr']:>8.2f}Cr  buy{a['buyCr']:>7.2f}/sell{a['sellCr']:>7.2f} "
            f"deals {a['buys'] + a['sells']:>2}  insti {a['nInsti']}  days {a['nDays']}  [{a['status']}]")
    if weekly:
        log("-" * 72)
        log(f"  WEEKLY TOP5 (delivery buying % change | {weekly['curDate']} vs {weekly['refDate']} | "
            f"coverage {weekly['coverage']}/500) [{weekly['status']}]")
        for i, r in enumerate(weekly["rows"], 1):
            log(f"    {i}. {r['sym']:<12} buy +{r['buyPct']:>7.1f}%  deliv% {r['dpRef']}->{r['dpCur']}  "
                f"instiNet ₹{r['instiNetCr']}Cr  close ₹{r['close']}")
    if fortnightly:
        log("-" * 72)
        log(f"  FORTNIGHTLY TOP5 (delivery buying % change | {fortnightly['curDate']} vs {fortnightly['refDate']} | "
            f"coverage {fortnightly['coverage']}/500) [{fortnightly['status']}]")
        for i, r in enumerate(fortnightly["rows"], 1):
            log(f"    {i}. {r['sym']:<12} buy +{r['buyPct']:>7.1f}%  deliv% {r['dpRef']}->{r['dpCur']}  "
                f"instiNet ₹{r['instiNetCr']}Cr  close ₹{r['close']}")
    if monthly:
        log("-" * 72)
        log(f"  MONTHLY TOP5 (delivery buying % change | {monthly['curDate']} vs {monthly['anchorDate']} | "
            f"coverage {monthly['coverage']}/500) [{monthly['status']}]")
        for i, r in enumerate(monthly["rows"], 1):
            log(f"    {i}. {r['sym']:<12} buy +{r['buyPct']:>7.1f}%  deliv% {r['dpRef']}->{r['dpCur']}  "
                f"instiNet ₹{r['instiNetCr']}Cr  close ₹{r['close']}")
    log("=" * 72)
    return payload


if __name__ == "__main__":
    try:
        scan()
    except Exception as exc:  # noqa: BLE001
        log(f"[FATAL] {exc}")
        raise
