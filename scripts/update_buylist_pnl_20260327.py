#!/usr/bin/env python3
import re
import requests
from pathlib import Path

MD = Path('docs/list/history/buylist/20260327buy.md')
text = MD.read_text(encoding='utf-8')
lines = text.splitlines()

# parse table rows
start = None
for i,l in enumerate(lines):
    if l.strip().startswith('| 排名'):
        start = i
        break
if start is None:
    raise SystemExit('table header not found')
# find end (--- separator line after table)
end = None
for j in range(start+1, len(lines)):
    if lines[j].strip().startswith('---') and lines[j].strip()=='---':
        end = j
        break
if end is None:
    end = len(lines)

table_lines = lines[start+2:end]  # skip header and separator

rows = []
for tl in table_lines:
    parts = [p.strip() for p in tl.split('|')[1:-1]]
    if len(parts) < 7:
        continue
    rank, code, name, industry, ref_price, qty, amount = parts[:7]
    try:
        ref_price_f = float(ref_price)
    except:
        ref_price_f = None
    qty_i = int(qty)
    rows.append({'rank':rank, 'code':code, 'name':name, 'industry':industry, 'ref_price':ref_price_f, 'qty':qty_i, 'amount':amount})

codes = [r['code'] for r in rows]
print('Fetching prices for codes:', codes)

# fetch current close prices from eastmoney datacenter
DATA_CENTER_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
HEADERS = {'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/'}
TOKEN = '894050c76af8597a853f5b408b759f5d'

filter_expr = '(SECURITY_CODE in ("' + '","'.join(codes) + '"))'
params = {
    'reportName':'RPT_DMSK_TS_STOCKNEW',
    'columns':'ALL',
    'pageNumber':1,
    'pageSize':500,
    'sortColumns':'SECURITY_CODE',
    'sortTypes': '1',
    'filter': filter_expr,
    'token': TOKEN,
}
resp = requests.get(DATA_CENTER_URL, params=params, headers=HEADERS, timeout=20)
resp.raise_for_status()
js = resp.json()
data = ((js or {}).get('result') or {}).get('data') or []

price_map = {}
for item in data:
    code = str(item.get('SECURITY_CODE') or item.get('code') or '')
    close = item.get('CLOSE_PRICE')
    try:
        close_f = float(close) if close is not None else None
    except:
        close_f = None
    price_map[code.zfill(6)] = close_f

# compute pnl
total_pnl = 0.0
out_lines = []
for r in rows:
    code = r['code']
    ref = r['ref_price']
    qty = r['qty']
    cur = price_map.get(code) or price_map.get(code.zfill(6))
    if cur is None or ref is None:
        pnl = None
        pnl_pct = None
    else:
        pnl = (cur - ref) * qty
        pnl_pct = (cur / ref - 1) * 100 if ref!=0 else None
        total_pnl += pnl
    out_lines.append({'code':code, 'name':r['name'], 'ref':ref, 'cur':cur, 'qty':qty, 'pnl':pnl, 'pnl_pct':pnl_pct, 'industry':r['industry'], 'amount':r['amount']})

# build new markdown
new_lines = []
new_lines.extend(lines[:start])
new_lines.append('| 排名 | 代码   | 名称       | 行业                               | 参考价（元） | 今日价（元） | 数量（股） | 金额（元）  | 盈亏（元） | 盈亏率 |')
new_lines.append('|------|--------|------------|------------------------------------|-------------:|-------------:|-----------:|------------:|-----------:|-------:|')
for i,r in enumerate(out_lines, start=1):
    ref_s = f"{r['ref']:.2f}" if r['ref'] is not None else ''
    cur_s = f"{r['cur']:.2f}" if r['cur'] is not None else ''
    pnl_s = f"{r['pnl']:.2f}" if r['pnl'] is not None else ''
    pct_s = f"{r['pnl_pct']:.2f}%" if r['pnl_pct'] is not None else ''
    new_lines.append(f"| {i}    | {r['code']} | {r['name']}   | {r['industry']}        | {ref_s}       | {cur_s}       | {r['qty']}       | {r['amount']}   | {pnl_s}   | {pct_s} |")

new_lines.append('')
new_lines.append(f'**合计股数：2,000 股（20 支 × 100 股）**  ')
new_lines.append(f'**合计金额：￥52,740.00 元**  ')
new_lines.append('')
new_lines.append(f'**总盈亏（元）：{total_pnl:.2f}**')

# write to a temp file and also print
OUT = Path('docs/list/history/buylist/20260327buy.updated.md')
OUT.write_text('\n'.join(new_lines), encoding='utf-8')
print('Wrote updated file to', OUT)
print('Total PnL:', total_pnl)
