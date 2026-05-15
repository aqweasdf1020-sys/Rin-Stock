#!/usr/bin/env python3
"""
scripts/fetcher.py — GitHub Actions 版資料抓取器
每日模式：TWSE（30秒）
季度模式：TWSE + FinMind（完整財務，需 token）

輸出：
  data/stock_data.js   → 網頁讀取（window.STOCK_DB）
  data/fetch_log.txt   → 抓取紀錄
"""

import argparse, json, logging, sqlite3, sys, time, urllib3
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT      = Path(__file__).parent.parent
DATA_DIR  = ROOT / "data"
DB_PATH   = DATA_DIR / "stock_data.db"
JS_PATH   = DATA_DIR / "stock_data.js"
LOG_PATH  = DATA_DIR / "fetch_log.txt"

DATA_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
    force=True,
)
log = logging.getLogger(__name__)

TWSE_BASE    = "https://openapi.twse.com.tw/v1"
FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"

HDR = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)", "Accept": "application/json"}

FULL_INTERVAL_DAYS = 80   # 超過此天數自動觸發完整抓取

# ── 欄位定義（43 欄）────────────────────────────────
COLS = [
    "code","name",
    "close","open_price","high","low","volume",
    "pe","pb","div_yield","cash_div",
    "eps","eps_prev","gross_margin","op_margin","net_margin",
    "revenue","revenue_prev","bps","roe","roa",
    "current_ratio","quick_ratio","debt_ratio","net_debt",
    "fcf","fcf_prev","op_cashflow","capex",
    "roic","interest_coverage",
    "z_x1","z_x2","z_x3","z_x4","z_x5",
    "eps_growth","rev_growth","fcf_growth","div_growth",
    "market_cap","shares","updated",
]


# ── 工具 ────────────────────────────────────────────
def _f(v):
    if v is None or str(v).strip() in ("","--","N/A","nan","null"): return None
    try: return float(str(v).replace(",","").strip())
    except: return None

def fetch(url, params=None, verify=True, retries=2):
    for attempt in range(retries + 1):
        for v in ([True, False] if verify else [False]):
            try:
                r = requests.get(url, headers=HDR, params=params,
                                 timeout=30, verify=v)
                if r.status_code == 402: return None   # FinMind 額度
                r.raise_for_status()
                return r.json()
            except requests.exceptions.SSLError:
                if v: continue
            except requests.exceptions.Timeout:
                log.warning(f"  逾時，重試 {attempt+1}/{retries}")
                time.sleep(2)
                break
            except Exception as e:
                log.error(f"  失敗: {e}")
                return {}
    return {}

def fm_fetch(dataset, stock_id, token, start):
    url = FINMIND_BASE
    params = {"dataset": dataset, "data_id": stock_id, "start_date": start}
    headers = {**HDR}
    if token: headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20, verify=False)
        if r.status_code == 402: return None
        if r.status_code != 200: return {}
        rows = r.json().get("data", [])
        by_date = {}
        for row in rows:
            d,t,v = row.get("date",""), row.get("type",""), _f(row.get("value"))
            by_date.setdefault(d, {})[t] = v
        return by_date
    except Exception as e:
        log.debug(f"  FinMind {dataset}/{stock_id}: {e}")
        return {}

def latest_two(by_date):
    if not by_date: return {},{}
    dates = sorted(by_date, reverse=True)
    return by_date[dates[0]], (by_date[dates[1]] if len(dates)>1 else {})


# ── DB ──────────────────────────────────────────────
def init_db(con):
    defs = []
    for c in COLS:
        if c == "code": defs.append("code TEXT PRIMARY KEY")
        elif c in ("name","volume","updated"): defs.append(f"{c} TEXT")
        else: defs.append(f"{c} REAL")
    con.executescript(f"""
    CREATE TABLE IF NOT EXISTS stocks ({",".join(defs)});
    CREATE TABLE IF NOT EXISTS fm_cache (
        code TEXT, dataset TEXT, fetched TEXT, data_json TEXT,
        PRIMARY KEY(code,dataset)
    );
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    # Auto-add missing columns (upgrade)
    cur = con.cursor()
    cur.execute("PRAGMA table_info(stocks)")
    existing = {r[1] for r in cur.fetchall()}
    for c in COLS:
        if c not in existing:
            typ = "TEXT" if c in ("name","volume","updated") else "REAL"
            con.execute(f"ALTER TABLE stocks ADD COLUMN {c} {typ}")
            log.info(f"  DB+欄位: {c}")
    con.commit()

def get_meta(con, key): 
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else None

def set_meta(con, key, val):
    con.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key,val)); con.commit()

def load_fm_cache(con, code, ds):
    r = con.execute("SELECT fetched,data_json FROM fm_cache WHERE code=? AND dataset=?",
                    (code,ds)).fetchone()
    return json.loads(r[1]) if r else None

def save_fm_cache(con, code, ds, data):
    con.execute("INSERT OR REPLACE INTO fm_cache VALUES(?,?,?,?)",
                (code, ds, date.today().isoformat(), json.dumps(data or {})))


# ── FinMind 解析 ─────────────────────────────────────
def parse_financials(income_bd, balance_bd, cash_bd):
    f = {}
    # 損益表
    t0,t1 = latest_two(income_bd or {})
    if t0:
        rev=t0.get("Revenue"); gp=t0.get("GrossProfit")
        op=t0.get("OperatingIncome"); ebit=t0.get("EBIT") or op
        ni=t0.get("NetIncome") or t0.get("ProfitAttributableToOwnersOfParent")
        eps0=t0.get("EPS"); eps1=t1.get("EPS") if t1 else None
        rev1=t1.get("Revenue") if t1 else None
        f.update({
            "eps":eps0, "eps_prev":eps1, "revenue":rev, "revenue_prev":rev1,
            "gross_margin": gp/rev*100  if gp  and rev  else None,
            "op_margin":    op/rev*100  if op  and rev  else None,
            "net_margin":   ni/rev*100  if ni  and rev  else None,
            "eps_growth":   (eps0-eps1)/abs(eps1)*100 if eps0 and eps1 and eps1!=0 else None,
            "rev_growth":   (rev-rev1)/abs(rev1)*100  if rev  and rev1 and rev1!=0  else None,
            "_ni":ni, "_ebit":ebit, "_rev":rev,
        })
    # 資產負債表
    t0,_ = latest_two(balance_bd or {})
    if t0:
        ta=t0.get("TotalAssets"); tl=t0.get("TotalLiabilities")
        eq=t0.get("TotalEquity"); ca=t0.get("CurrentAssets"); cl=t0.get("CurrentLiabilities")
        inv=t0.get("Inventories") or 0; re_=t0.get("RetainedEarnings")
        std=t0.get("ShortTermBorrowings") or 0; ltd=t0.get("LongTermBorrowings") or 0
        csh=t0.get("CashAndCashEquivalents") or 0
        int_exp=t0.get("InterestExpense") or t0.get("FinanceCosts")
        ni=f.get("_ni"); ebit=f.get("_ebit"); rev=f.get("_rev")
        invested=(eq or 0)+std+ltd
        f.update({
            "bps":            t0.get("BookValuePerShare"),
            "current_ratio":  ca/cl           if ca and cl and cl!=0 else None,
            "quick_ratio":    (ca-inv)/cl      if ca and cl and cl!=0 else None,
            "debt_ratio":     tl/ta*100        if tl and ta else None,
            "net_debt":       (std+ltd-csh)/1e8 if (std or ltd) else None,
            "roe":  ni/eq*100  if ni and eq  else None,
            "roa":  ni/ta*100  if ni and ta  else None,
            "roic": ebit*0.75/invested*100 if ebit and invested and invested!=0 else None,
            "interest_coverage": ebit/int_exp if ebit and int_exp and int_exp!=0 else None,
            "z_x1": (ca-cl)/ta  if ca and cl and ta else None,
            "z_x2": re_/ta      if re_ and ta       else None,
            "z_x3": ebit/ta     if ebit and ta      else None,
            "z_x5": rev/ta      if rev and ta        else None,
            "_ta":ta, "_tl":tl, "_eq":eq,
        })
    # 現金流量表
    t0,t1 = latest_two(cash_bd or {})
    if t0:
        op_cf=t0.get("CashFlowsFromOperatingActivities")
        capex=t0.get("AcquisitionOfPropertyPlantAndEquipment") or \
              t0.get("PaymentsForPropertyPlantAndEquipment")
        op_cf1=t1.get("CashFlowsFromOperatingActivities") if t1 else None
        capex1=t1.get("AcquisitionOfPropertyPlantAndEquipment") if t1 else None
        fcf0=(op_cf-abs(capex))/1e8 if op_cf and capex else (op_cf/1e8 if op_cf else None)
        fcf1=(op_cf1-abs(capex1))/1e8 if op_cf1 and capex1 else None
        f.update({
            "op_cashflow":op_cf, "capex":abs(capex) if capex else None,
            "fcf":fcf0, "fcf_prev":fcf1,
            "fcf_growth":(fcf0-fcf1)/abs(fcf1)*100 if fcf0 and fcf1 and fcf1!=0 else None,
        })
    return f

def enrich(f, close):
    """用收盤價補算市值與股數"""
    eq=f.get("_eq"); bps=f.get("bps")
    if eq and bps and bps!=0 and close:
        shares = eq / bps / 1000      # 百萬股
        f["_shares"]  = shares
        f["_mktcap"]  = close * shares * 1e6 / 1e8   # 億元
    return f

def make_row(code, b, p, f, divs, today):
    tl=f.get("_tl"); mktcap=f.get("_mktcap"); shares=f.get("_shares")
    z_x4=(mktcap/(tl/1e8)) if mktcap and tl and tl!=0 else None
    row = (
        code,                       b.get("name") or "",
        p.get("close"),             p.get("open_price"),
        p.get("high"),              p.get("low"),
        p.get("volume"),
        b.get("pe"),                b.get("pb"),
        b.get("div_yield"),         divs.get(code),
        f.get("eps"),               f.get("eps_prev"),
        f.get("gross_margin"),      f.get("op_margin"),    f.get("net_margin"),
        f.get("revenue"),           f.get("revenue_prev"),
        f.get("bps"),               f.get("roe"),           f.get("roa"),
        f.get("current_ratio"),     f.get("quick_ratio"),
        f.get("debt_ratio"),        f.get("net_debt"),
        f.get("fcf"),               f.get("fcf_prev"),
        f.get("op_cashflow"),       f.get("capex"),
        f.get("roic"),              f.get("interest_coverage"),
        f.get("z_x1"),              f.get("z_x2"),
        f.get("z_x3"),              z_x4,
        f.get("z_x5"),
        f.get("eps_growth"),        f.get("rev_growth"),
        f.get("fcf_growth"),        f.get("div_growth"),
        mktcap,                     shares,
        today,
    )
    assert len(row) == len(COLS), f"{code}: {len(row)} != {len(COLS)}"
    return row


# ── 輸出 JS ──────────────────────────────────────────
def export_js(con, mode, today, last_full):
    cur = con.cursor()
    cur.execute("SELECT * FROM stocks ORDER BY code")
    cols_db = [d[0] for d in cur.description]
    stocks = {}
    for row in cur.fetchall():
        d = dict(zip(cols_db, row))
        stocks[d.pop("code")] = d

    payload = {
        "updated":        today,
        "generated_at":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total":          len(stocks),
        "fetch_mode":     mode,
        "last_full_fetch":last_full or "未曾執行",
        "sources": {
            "twse":    sum(1 for v in stocks.values() if v.get("close")),
            "finmind": sum(1 for v in stocks.values() if v.get("eps")),
        },
        "stocks": stocks,
    }
    JS_PATH.write_text(
        "window.STOCK_DB=" +
        json.dumps(payload, ensure_ascii=False, separators=(",",":")) + ";\n",
        encoding="utf-8"
    )
    s = payload["sources"]
    log.info(f"JS 輸出：{len(stocks)} 支 TWSE:{s['twse']} FinMind:{s['finmind']}")
    return payload


# ══════════════════════════════════════════════════════
def run(token="", force_full=False):
    today     = date.today().isoformat()
    start_fin = (date.today() - timedelta(days=730)).isoformat()

    con = sqlite3.connect(DB_PATH)
    init_db(con)

    last_full = get_meta(con, "last_full_fetch")
    need_full = force_full
    if not need_full:
        if not last_full:
            need_full = True
            log.info("首次執行 → 完整抓取")
        else:
            days = (date.today() - date.fromisoformat(last_full)).days
            if days >= FULL_INTERVAL_DAYS:
                need_full = True
                log.info(f"距上次完整抓取 {days} 天 → 自動觸發")

    mode = "完整（季度）" if need_full else "每日（TWSE）"
    log.info(f"═══ 抓取開始 {today}【{mode}】═══")

    # ── TWSE ──────────────────────────────────────────
    bwibbu, prices, divs = {}, {}, {}

    def twse_get(path):
        url = TWSE_BASE + path
        log.info(f"[TWSE] {path}")
        data = fetch(url)
        if isinstance(data, list) and data:
            log.info(f"  → {len(data)} 筆")
            return data
        return []

    for r in twse_get("/exchangeReport/BWIBBU_ALL"):
        code = str(r.get("Code") or "").strip()
        if code.isdigit() and len(code) == 4:
            bwibbu[code] = {
                "name":      str(r.get("Name") or "").strip(),
                "pe":        _f(r.get("PEratio")),
                "pb":        _f(r.get("PBratio")),
                "div_yield": _f(r.get("DividendYield")),
            }
    time.sleep(2)

    for r in twse_get("/exchangeReport/STOCK_DAY_ALL"):
        code = str(r.get("Code") or "").strip()
        if code.isdigit() and len(code) == 4:
            prices[code] = {
                "close":      _f(r.get("ClosingPrice")),
                "open_price": _f(r.get("OpeningPrice")),
                "high":       _f(r.get("HighestPrice")),
                "low":        _f(r.get("LowestPrice")),
                "volume":     str(r.get("TradeVolume") or ""),
            }
    time.sleep(2)

    for r in twse_get("/opendata/t187ap45_L"):
        code = str(r.get("公司代號") or "").strip()
        cash = _f(r.get("股東配發-盈餘分配之現金股利(元/股)") or
                  r.get("現金股利(元/股)") or r.get("現金股利"))
        if code and cash is not None:
            divs[code] = cash
    time.sleep(1)

    all_codes = sorted(set(bwibbu) | set(prices))
    log.info(f"TWSE 合計：{len(all_codes)} 支")

    # ── FinMind（季度模式）──────────────────────────
    fin_cache = {code: {
        "income":  load_fm_cache(con, code, "TaiwanStockFinancialStatements"),
        "balance": load_fm_cache(con, code, "TaiwanStockBalanceSheet"),
        "cash":    load_fm_cache(con, code, "TaiwanStockCashFlowsStatement"),
    } for code in all_codes}

    cached_n = sum(1 for v in fin_cache.values() if v["income"] is not None)
    log.info(f"快取財務資料：{cached_n} 支")

    if need_full:
        DS = ["TaiwanStockFinancialStatements",
              "TaiwanStockBalanceSheet",
              "TaiwanStockCashFlowsStatement"]
        fm_ok = 0; quota_ok = True

        for i, code in enumerate(all_codes):
            if not quota_ok: break
            changed = False
            for ds, key in zip(DS, ["income","balance","cash"]):
                if fin_cache[code][key] is not None: continue
                if not quota_ok: break
                bd = fm_fetch(ds, code, token, start_fin)
                if bd is None:
                    log.warning(f"  FinMind 額度用盡（{i}/{len(all_codes)}）")
                    quota_ok = False; break
                save_fm_cache(con, code, ds, bd)
                fin_cache[code][key] = bd
                changed = True
                time.sleep(0.4)
            if changed: fm_ok += 1
            if (i+1) % 50 == 0:
                con.commit()
                log.info(f"  FinMind {i+1}/{len(all_codes)}，新抓：{fm_ok}")

        con.commit()
        log.info(f"FinMind 完成：新抓 {fm_ok} 支")
        set_meta(con, "last_full_fetch", today)

    # ── 合併寫入 ──────────────────────────────────────
    batch = []
    for code in all_codes:
        b = bwibbu.get(code, {})
        p = prices.get(code, {})
        c = fin_cache.get(code, {})
        f = parse_financials(c.get("income"), c.get("balance"), c.get("cash"))
        enrich(f, p.get("close"))
        f["div_growth"] = None
        batch.append(make_row(code, b, p, f, divs, today))

    ph = ",".join(["?"]*len(COLS))
    con.executemany(f"INSERT OR REPLACE INTO stocks VALUES({ph})", batch)
    con.commit()
    log.info(f"stocks 寫入：{len(batch)} 筆")

    export_js(con, mode, today, get_meta(con, "last_full_fetch"))
    con.close()
    log.info("═══ 完成 ═══")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--token",  default="", help="FinMind API Token")
    ap.add_argument("--full",   action="store_true", help="強制完整抓取")
    args = ap.parse_args()
    run(token=args.token, force_full=args.full)
