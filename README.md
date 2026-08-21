# 🐘 INSTI TRACKER — NSE Institutional Flow Dashboard

**Institutions kis stock me buying kar rahe hain — ab roz track karo.**

NSE ke **official public data** se banaya gaya daily scanner:
| Data | Source |
|---|---|
| BULK DEALS (≥0.5% equity ke bade deals) | `archives.nseindia.com/content/equities/bulk.csv` |
| BLOCK DEALS (>₹10Cr window trades) | `archives.nseindia.com/content/equities/block.csv` |
| FII/DII daily net investment | `nseindia.com/api/fiidiiTradeReact` |

Har deal ka client auto-classify hota hai:
- 🟦 **INSTITUTION** — Mutual Fund, Insurance, Bank, FII/FPI, AMC, Pension, Securities/Broking, AIF (130+ keyword patterns)
- 🟨 **PROMOTER** — "PROMOTER", "HUF", "FOUNDER", "DIRECTOR", company-namesake entities
- ⬜ **OTHER** — individuals, LLPs, ventures, baaki

## 📊 Dashboard sections
- **🏦 FII vs DII** — aaj ka net investment + pichhle 15 sessions ki history (auto-jamti hai)
- **🔥 INSTITUTIONAL ACCUMULATION (7D)** — kis stock me institutions net buying/distribution kar rahe hain (net ₹Cr leaderboard, bulk/block deals se)
- **🚀 WEEKLY TOP 5 — Buying % Change (Nifty 500)** — delivery volume me sabse badi weekly % jump (7 din pehle ke trading din se compare). **Har Friday night final**, baaki din daily refresh. Saare 500 stocks scan hote hain.
- **🗓️ FORTNIGHTLY TOP 5 — Buying % Change (Nifty 500)** — delivery volume me sabse badi 15-din (fortnight) % jump (15 din pehle ke trading din se compare). **Har month ki 16th ko final** (16th holiday ho to agla trading din), baaki din daily refresh. Saare 500 stocks scan hote hain.
- **📅 MONTHLY TOP 5 — Buying % Change (Nifty 500)** — delivery volume me sabse badi monthly % jump (pichhle month-end se compare). **Har month-end final**, baaki din daily refresh. Saare 500 stocks scan hote hain.
- **📋 BULK & BLOCK DEALS** — full detail table (date-wise history, class/side/type/symbol/min-value filters)
- **🗂 SECTOR FLOW (7D)** — kis sector me institutional paisa aa raha hai / nikal raha hai

**Weekly/Fortnightly/Monthly Top 5 kaise compute hota hai:**
1. Har din NSE ka official **bhavcopy** (`sec_bhavdata_full` CSV) fetch hota hai — har stock ka `DELIV_QTY` (delivery quantity) aur `DELIV_PER` (delivery %)
2. **Weekly % change** = aaj ki delivery qty vs 7 din pehle wale trading din ki delivery qty
3. **Fortnightly % change** = aaj ki delivery qty vs 15 din pehle wale trading din ki delivery qty
4. **Monthly % change** = aaj ki delivery qty vs pichhle month ke aakhri trading din ki delivery qty
5. Delivery data = **institutional accumulation ka proxy** (institutions delivery-based buying karte hain, intraday traders nahi). Har row me delivery% ka change bhi dikhta hai + agar us stock me bulk/block deals hue hain to wo overlay bhi.
6. Noise filter: ref din pe kam se kam 50,000 delivery qty + delivery% ≥ 5% honi chahiye.
7. Final cadence: Weekly = har Friday raat (18:30 IST scan) • Fortnightly = har month ki 16th • Monthly = har month-end. Baaki sab din rankings daily refresh hoti hain "IN PROGRESS" tag ke saath.

**Use kaise karo:** Weekly/Monthly Top 5 ko apne GTF zone dashboard (strict-pure-stocks / all-timeframe-stocks) se cross-check karo — delivery buying boom + GTF demand zone dono ho to setup quality badh jati hai.

## 🔒 Password
`7004602` (index.html me change kar sakte ho)

## 🚀 GitHub pe setup (5 minute — same pattern jaise baaki dashboards)

1. Naya repo banao: **insti-tracker** (Public)
2. Saari files upload karo (`.github` folder + `history` folder bhi — hidden folders dikhao!)
   - `index.html`, `insti_scanner.py`, `nse_symbols.csv`, `nifty500_universe.csv`
   - `insti_live_data.json`, `INSTI_Deals.csv`, `INSTI_Accumulation.csv`
   - `history/all.json`
3. Settings → Actions → General → Workflow permissions → **"Read and write contents"** ✅
4. Settings → Pages → Source: **Deploy from branch → main → root** ✅
5. 2-3 minute me live: `https://USERNAME.github.io/insti-tracker/`

> ⚠️ Pehla scan: Actions tab → **Run workflow** → green tick ✔️
> Uske baad roz **Mon–Fri 18:30 IST** auto-scan chalega (NSE deals shaam ko update hote hain).

## 📁 Files
| File | Kaam |
|---|---|
| `insti_scanner.py` | Scanner — NSE bulk/block/FII-DII + bhavcopy delivery fetch + classification + weekly/monthly top5 (GitHub Actions se roz chalta hai) |
| `index.html` | Dashboard UI — data `insti_live_data.json` + `history/all.json` se load karta hai (embedded snapshot fallback bhi hai) |
| `insti_live_data.json` | Roz ka live scan output (bot isi ko update karta hai) |
| `history/all.json` | Date-wise deals + fiidii history (last 60 din auto) |
| `history/delivery.json` | Date-wise bhavcopy delivery data — weekly/monthly % change compute ke liye (last 75 din auto) |
| `nse_symbols.csv` | Full NSE list (2,075 symbols) — company name mapping |
| `nifty500_universe.csv` | Nifty 500 industry mapping — universe + sector flow ke liye |
| `INSTI_Deals.csv` / `INSTI_Accumulation.csv` | Roz ke CSV exports |
| `INSTI_WeeklyTop5.csv` / `INSTI_FortnightlyTop5.csv` / `INSTI_MonthlyTop5.csv` | Weekly (Fri final) + Fortnightly (16th of month final) + Monthly (month-end final) buying % change leaderboards |

## ⚠️ Limitations (transparent raho)
- History deployment ke din se shuru hoti hai (NSE purane din ke deals CSV archive nahi rakhta) — roz ek naya din judta jayega
- Client classification keyword-based hai — koi galat class ho to deal table me client name khud judge karo
- Bulk deals me institutions ke alawa promoters/individuals bhi hote hain — isliye class filter zaroor use karo
- Institutional deals ≠ price guarantee — entry/exit apne zone system se lo

**Educational project — investment advice nahi.**
