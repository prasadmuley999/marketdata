import os
import re
import glob
import urllib.request
import smtplib
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

# Target Symbols list
TARGET_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", 
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BPCL", 
    "BHARTIARTL", "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB", 
    "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", 
    "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR", 
    "ICICIBANK", "INDIGO", "INDUSINDBK", "INFY", "JIOFIN", 
    "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI", 
    "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC", "POWERGRID", 
    "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA", 
    "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS", "TECHM", 
    "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"
]

EXCEL_FILE = "NSE_Merged_Reports.xlsx"

# COLOR CODING CONFIGURATION
# Set to True to highlight price drops in Green and increases in Red as specified.
INVERT_COLORS = True 

def get_date_from_filename(filename):
    """Parses date from MTO_DDMMYYYY.DAT and returns a real datetime object for chronological sorting."""
    match = re.search(r'MTO_(\d{8})\.DAT', filename)
    if match:
        try:
            return datetime.strptime(match.group(1), '%d%m%Y')
        except ValueError:
            pass
    return datetime.min

def is_valid_file(filepath):
    if not os.path.exists(filepath):
        return False
    if os.path.getsize(filepath) < 500:
        return False
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            first_line = f.readline()
            if '<html' in first_line.lower() or '<!doctype' in first_line.lower():
                return False
    except Exception:
        return False
    return True

def download_file(url, filepath):
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    )
    try:
        with urllib.request.urlopen(req) as response:
            with open(filepath, 'wb') as f:
                f.write(response.read())
        
        if is_valid_file(filepath):
            return True
        else:
            if os.path.exists(filepath):
                os.remove(filepath)
    except Exception:
        if os.path.exists(filepath):
            os.remove(filepath)
    return False

def sync_reports():
    os.makedirs('reports', exist_ok=True)
    utc_now = datetime.utcnow()
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    
    keep_files = set()
    count = 0
    current_date = ist_now
    checked_days = 0
    
    print("Synchronizing reports...")
    
    while count < 7 and checked_days < 30:
        checked_days += 1
        if current_date.weekday() >= 5:
            current_date -= timedelta(days=1)
            continue
            
        date_str = current_date.strftime('%d%m%Y')
        filename = f"MTO_{date_str}.DAT"
        filepath = os.path.join('reports', filename)
        
        if os.path.exists(filepath) and is_valid_file(filepath):
            keep_files.add(filename)
            count += 1
        else:
            url = f"https://archives.nseindia.com/archives/equities/mto/{filename}"
            if download_file(url, filepath):
                print(f"-> Successfully downloaded: {filename}")
                keep_files.add(filename)
                count += 1
                
        current_date -= timedelta(days=1)
        
    all_files = glob.glob('reports/MTO_*.DAT')
    for f in all_files:
        basename = os.path.basename(f)
        if basename not in keep_files:
            try:
                os.remove(f)
            except Exception:
                pass
                
    # Sort files chronologically descending (newest first)
    keep_files_list = list(keep_files)
    keep_files_list.sort(key=get_date_from_filename, reverse=True)
    return keep_files_list

def parse_mto_file(filepath):
    rows = []
    if not os.path.exists(filepath):
        return pd.DataFrame(columns=['SYMBOL', 'DEL_QTY', 'DEL_PCT'])
    with open(filepath, 'r') as f:
        for line in f:
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 7 and parts[0] == '20':
                symbol = parts[2]
                series = parts[3]
                try:
                    del_qty = int(parts[5])
                    del_pct = float(parts[6])
                except ValueError:
                    del_qty = None
                    del_pct = None
                rows.append({'SYMBOL': symbol, 'SERIES': series, 'DEL_QTY': del_qty, 'DEL_PCT': del_pct})
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df[df['SERIES'] == 'EQ']
    else:
        df = pd.DataFrame(columns=['SYMBOL', 'DEL_QTY', 'DEL_PCT'])
    return df

def fetch_historical_price_data(dates_list):
    if not dates_list:
        return pd.DataFrame(), pd.DataFrame()
    min_date = min(dates_list)
    max_date = max(dates_list)
    
    start_date_str = (min_date - timedelta(days=7)).strftime('%Y-%m-%d')
    end_date_str = (max_date + timedelta(days=3)).strftime('%Y-%m-%d')
    
    tickers = [f"{sym}.NS" for sym in TARGET_SYMBOLS]
    print(f"Downloading historical pricing from {start_date_str} to {end_date_str}...")
    try:
        price_data = yf.download(tickers, start=start_date_str, end=end_date_str, progress=False)
        close_df = price_data['Close'] if 'Close' in price_data else price_data
        change_df = close_df.pct_change() * 100
        
        close_df.index = pd.to_datetime(close_df.index).date
        change_df.index = pd.to_datetime(change_df.index).date
        return close_df, change_df
    except Exception as e:
        print(f"Error fetching historical prices: {e}")
        return pd.DataFrame(), pd.DataFrame()

def get_recipient_email():
    if os.path.exists('email.txt'):
        try:
            with open('email.txt', 'r') as f:
                return f.read().strip()
        except Exception:
            pass
    return None

def build_master_dashboard_data(valid_filenames):
    """Processes historical daily reports, performs calculations, and returns sorted dashboard dataframes."""
    # Group dates from newest (D-0) to oldest (D-4)
    target_filenames = valid_filenames[:5]
    
    dates = []
    for f in target_filenames:
        dates.append(get_date_from_filename(f))
        
    close_df, change_df = fetch_historical_price_data(dates)
    
    # Sort filenames and dates chronologically ascending: oldest first (D-4, D-3, D-2, D-1, D-0)
    chronological_filenames = sorted(target_filenames, key=get_date_from_filename)
    chronological_dates = sorted(dates)
    chronological_date_strs = [d.strftime('%d-%b-%Y') for d in chronological_dates]
    
    today_str = dates[0].strftime('%d-%b-%Y')
    yesterday_str = dates[1].strftime('%d-%b-%Y')
    hist_date_strs = [dates[j].strftime('%d-%b-%Y') for j in range(1, 5)] # past 4 days
    
    # Parse daily reports into fast lookups
    mto_lookups = {}
    for filename in target_filenames:
        dt = get_date_from_filename(filename)
        date_str = dt.strftime('%d-%b-%Y')
        df_mto = parse_mto_file(os.path.join('reports', filename))
        mto_lookups[date_str] = df_mto.set_index('SYMBOL').to_dict('index')

    rows = []
    for symbol in TARGET_SYMBOLS:
        row_dict = {'SYMBOL': symbol}
        ticker = f"{symbol}.NS"
        
        # 1. Close Prices (Oldest to Newest)
        for d_str, d_obj in zip(chronological_date_strs, chronological_dates):
            col_name = f"Price ({d_str})"
            price_val = None
            if not close_df.empty and ticker in close_df.columns:
                target_date = d_obj.date()
                if target_date in close_df.index:
                    val = close_df.loc[target_date, ticker]
                    if isinstance(val, pd.Series):
                        val = val.iloc[0]
                    if pd.notna(val):
                        price_val = val
            row_dict[col_name] = price_val
            
        # 2. Delivery Percentages (Oldest to Newest)
        for d_str in chronological_date_strs:
            col_name = f"Del% ({d_str})"
            val = None
            if d_str in mto_lookups and symbol in mto_lookups[d_str]:
                val = mto_lookups[d_str][symbol].get('DEL_PCT')
            row_dict[col_name] = val
            
        # 3. Delivery Quantities (Oldest to Newest)
        for d_str in chronological_date_strs:
            col_name = f"Del Qty ({d_str})"
            val = None
            if d_str in mto_lookups and symbol in mto_lookups[d_str]:
                val = mto_lookups[d_str][symbol].get('DEL_QTY')
            row_dict[col_name] = val
            
        rows.append(row_dict)
        
    master_df = pd.DataFrame(rows)

    # Perform required comparisons
    t_pct_col = f"Del% ({today_str})"
    y_pct_col = f"Del% ({yesterday_str})"
    t_qty_col = f"Del Qty ({today_str})"
    y_qty_col = f"Del Qty ({yesterday_str})"
    t_prc_col = f"Price ({today_str})"
    y_prc_col = f"Price ({yesterday_str})"
    
    hist_pct_cols = [f"Del% ({d})" for d in hist_date_strs]
    hist_qty_cols = [f"Del Qty ({d})" for d in hist_date_strs]

    master_df['Diff % vs Yesterday'] = master_df[t_pct_col] - master_df[y_pct_col]
    master_df['Diff % vs Avg4'] = master_df[t_pct_col] - master_df[hist_pct_cols].mean(axis=1)
    
    master_df['Diff Qty vs Yesterday'] = master_df[t_qty_col] - master_df[y_qty_col]
    master_df['Diff Qty vs Avg4'] = master_df[t_qty_col] - master_df[hist_qty_cols].mean(axis=1)
    
    # Price Change: Absolute value difference (Today Price - Yesterday Price)
    master_df['Price Change'] = master_df[t_prc_col] - master_df[y_prc_col]

    master_df = master_df.sort_values(by='Diff % vs Yesterday', ascending=False, na_position='last')
    
    gainers = []
    losers = []
    if not change_df.empty:
        today_date = dates[0].date()
        today_changes = change_df.loc[today_date] if today_date in change_df.index else pd.Series()
        if not today_changes.empty:
            today_changes.index = [t.replace('.NS', '') for t in today_changes.index]
            today_prices = close_df.loc[today_date]
            today_prices.index = [t.replace('.NS', '') for t in today_prices.index]
            
            summary = pd.DataFrame({
                'SYMBOL': today_changes.index,
                'PRICE': today_prices.values,
                'PCT_CHANGE': today_changes.values
            }).dropna()
            
            summary = summary[summary['SYMBOL'].isin(TARGET_SYMBOLS)]
            gainers = summary.sort_values(by='PCT_CHANGE', ascending=False).head(4).to_dict('records')
            losers = summary.sort_values(by='PCT_CHANGE', ascending=True).head(4).to_dict('records')

    # Retain index ranges to cleanly map double-headers
    chronological_indices = list(range(4, -1, -1))
    return master_df, chronological_indices, chronological_dates, gainers, losers

def generate_html_content(df, dates, chronological_indices, gainers, losers):
    """Generates an institutional-grade live dashboard with double-header grouping."""
    
    # Header Row 1: Primary structural groupings
    header_row_1 = '<tr class="bg-slate-800 text-slate-300 border-b border-slate-700 text-xs font-bold uppercase tracking-wider text-center">\n'
    header_row_1 += '  <th rowspan="2" onclick="sortTable(0)" class="px-4 py-3 text-left cursor-pointer hover:bg-slate-700 min-w-[140px] vertical-align-middle">Symbol</th>\n'
    header_row_1 += '  <th colspan="5" class="px-4 py-2 border-l border-slate-700 text-center">Close Prices</th>\n'
    header_row_1 += '  <th colspan="5" class="px-4 py-2 border-l border-slate-700 text-center">Delivery %</th>\n'
    header_row_1 += '  <th colspan="5" class="px-4 py-2 border-l border-slate-700 text-center">Delivery Quantity</th>\n'
    header_row_1 += f'  <th colspan="2" class="px-4 py-2 border-l border-slate-700 text-center">Del% Diff</th>\n'
    header_row_1 += f'  <th colspan="2" class="px-4 py-2 border-l border-slate-700 text-center">Del Qty Diff</th>\n'
    header_row_1 += f'  <th rowspan="2" onclick="sortTable(20)" class="px-4 py-3 text-right cursor-pointer hover:bg-slate-700 border-l border-slate-700 vertical-align-middle">Price Change (Yest vs Today)</th>\n'
    header_row_1 += '</tr>\n'

    # Header Row 2: Sub-headers displaying chronological dates
    header_row_2 = '<tr class="bg-slate-800/80 text-slate-400 border-b border-slate-700 text-[10px] uppercase tracking-wider text-right">\n'
    
    sub_idx = 1
    # 1. Sub-headers for Prices (Cols 1 to 5)
    for i in range(5):
        date_str = dates[i].strftime('%d-%b')
        header_row_2 += f'  <th onclick="sortTable({sub_idx})" class="px-4 py-2 cursor-pointer hover:bg-slate-700 border-l border-slate-700">{date_str}</th>\n'
        sub_idx += 1
        
    # 2. Sub-headers for Delivery % (Cols 6 to 10)
    for i in range(5):
        date_str = dates[i].strftime('%d-%b')
        header_row_2 += f'  <th onclick="sortTable({sub_idx})" class="px-4 py-2 cursor-pointer hover:bg-slate-700 border-l border-slate-700">{date_str}</th>\n'
        sub_idx += 1
        
    # 3. Sub-headers for Delivery Quantity (Cols 11 to 15)
    for i in range(5):
        date_str = dates[i].strftime('%d-%b')
        header_row_2 += f'  <th onclick="sortTable({sub_idx})" class="px-4 py-2 cursor-pointer hover:bg-slate-700 border-l border-slate-700">{date_str}</th>\n'
        sub_idx += 1
        
    # 4. Sub-headers for Del% Diff (Cols 16 & 17)
    header_row_2 += f'  <th onclick="sortTable({sub_idx})" class="px-4 py-2 cursor-pointer hover:bg-slate-700 border-l border-slate-700">vs Yest</th>\n'
    header_row_2 += f'  <th onclick="sortTable({sub_idx+1})" class="px-4 py-2 cursor-pointer hover:bg-slate-700">vs Avg4</th>\n'
    sub_idx += 2
    
    # 5. Sub-headers for Del Qty Diff (Cols 18 & 19)
    header_row_2 += f'  <th onclick="sortTable({sub_idx})" class="px-4 py-2 cursor-pointer hover:bg-slate-700 border-l border-slate-700">vs Yest</th>\n'
    header_row_2 += f'  <th onclick="sortTable({sub_idx+1})" class="px-4 py-2 cursor-pointer hover:bg-slate-700">vs Avg4</th>\n'
    
    header_row_2 += '</tr>\n'

    # Build rows mapping corresponding grouped columns
    rows_html = ""
    for _, row in df.iterrows():
        sym = row['SYMBOL']
        rows_html += f'<tr class="border-b border-slate-700 hover:bg-slate-800 transition-colors">\n'
        rows_html += f'  <td class="px-4 py-3 text-sm font-bold text-slate-100">{sym}</td>\n'
        
        # 1. Close Prices (oldest to newest)
        for i in range(5):
            date_str = dates[i].strftime('%d-%b-%Y')
            val = row[f"Price ({date_str})"]
            val_str = f"₹{val:,.2f}" if pd.notna(val) else "—"
            rows_html += f'  <td class="px-4 py-3 text-sm text-right text-slate-300 border-l border-slate-75" data-sort="{val if pd.notna(val) else -1}">{val_str}</td>\n'
            
        # 2. Delivery % (oldest to newest)
        for i in range(5):
            date_str = dates[i].strftime('%d-%b-%Y')
            val = row[f"Del% ({date_str})"]
            val_str = f"{val:.2f}%" if pd.notna(val) else "—"
            rows_html += f'  <td class="px-4 py-3 text-sm text-right text-slate-400 border-l border-slate-75" data-sort="{val if pd.notna(val) else -1}">{val_str}</td>\n'
            
        # 3. Delivery Quantity (oldest to newest)
        for i in range(5):
            date_str = dates[i].strftime('%d-%b-%Y')
            val = row[f"Del Qty ({date_str})"]
            val_str = f"{int(val):,}" if pd.notna(val) else "—"
            rows_html += f'  <td class="px-4 py-3 text-sm text-right text-slate-400 border-l border-slate-75" data-sort="{val if pd.notna(val) else -1}">{val_str}</td>\n'
            
        # 4. Del% Diff (vs Yest, vs Avg4)
        for col_name in ['Diff % vs Yesterday', 'Diff % vs Avg4']:
            val = row[col_name]
            if pd.isna(val):
                rows_html += f'  <td class="px-4 py-3 text-sm text-right text-slate-500 border-l border-slate-75" data-sort="-999">—</td>\n'
            else:
                color_class = "text-emerald-400 font-semibold" if val > 0 else "text-rose-400 font-semibold" if val < 0 else "text-slate-400"
                sign = "+" if val > 0 else ""
                rows_html += f'  <td class="px-4 py-3 text-sm text-right {color_class} border-l border-slate-75" data-sort="{val}">{sign}{val:.2f}%</td>\n'
                
        # 5. Del Qty Diff (vs Yest, vs Avg4)
        for col_name in ['Diff Qty vs Yesterday', 'Diff Qty vs Avg4']:
            val = row[col_name]
            if pd.isna(val):
                rows_html += f'  <td class="px-4 py-3 text-sm text-right text-slate-500 border-l border-slate-75" data-sort="-999999999">—</td>\n'
            else:
                color_class = "text-emerald-400 font-semibold" if val > 0 else "text-rose-400 font-semibold" if val < 0 else "text-slate-400"
                sign = "+" if val > 0 else ""
                rows_html += f'  <td class="px-4 py-3 text-sm text-right {color_class} border-l border-slate-75" data-sort="{val}">{sign}{int(val):,}</td>\n'

        # 6. Price Change Abs (At the very end)
        val = row['Price Change']
        if pd.isna(val):
            rows_html += f'  <td class="px-4 py-3 text-sm text-right text-slate-500 border-l border-slate-75" data-sort="-999">—</td>\n'
        else:
            if INVERT_COLORS:
                color_class = "text-emerald-400 font-semibold" if val < 0 else "text-rose-400 font-semibold" if val > 0 else "text-slate-400"
            else:
                color_class = "text-emerald-400 font-semibold" if val > 0 else "text-rose-400 font-semibold" if val < 0 else "text-slate-400"
            sign = "+" if val > 0 else ""
            rows_html += f'  <td class="px-4 py-3 text-sm text-right {color_class} border-l border-slate-75" data-sort="{val}">{sign}₹{val:,.2f}</td>\n'
            
        rows_html += f'</tr>\n'

    # Compile Gainer / Loser Markup blocks
    gainers_html = "".join([
        f'<div class="bg-slate-800 border-l-4 border-emerald-500 rounded p-4 shadow-sm">'
        f'  <div class="text-xs text-slate-400 font-bold tracking-wider">{g["SYMBOL"]}</div>'
        f'  <div class="flex items-baseline justify-between mt-1">'
        f'    <span class="text-lg font-extrabold text-slate-100">₹{g["PRICE"]:.2f}</span>'
        f'    <span class="text-sm font-bold text-emerald-400">+{g["PCT_CHANGE"]:.2f}%</span>'
        f'  </div>'
        f'</div>' for g in gainers
    ])
    
    losers_html = "".join([
        f'<div class="bg-slate-800 border-l-4 border-rose-500 rounded p-4 shadow-sm">'
        f'  <div class="text-xs text-slate-400 font-bold tracking-wider">{l["SYMBOL"]}</div>'
        f'  <div class="flex items-baseline justify-between mt-1">'
        f'    <span class="text-lg font-extrabold text-slate-100">₹{l["PRICE"]:.2f}</span>'
        f'    <span class="text-sm font-bold text-rose-400">{l["PCT_CHANGE"]:.2f}%</span>'
        f'  </div>'
        f'</div>' for l in losers
    ])

    last_updated = datetime.now().strftime('%d-%b-%Y %I:%M %p')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NSE Equity Delivery & Price Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>body {{ background-color: #0f172a; }}</style>
</head>
<body class="text-slate-100 font-sans min-h-screen font-medium">
    <div class="max-w-7xl mx-auto px-4 py-8">
        <div class="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-slate-700 pb-6 mb-8 gap-4">
            <div>
                <h1 class="text-3xl font-extrabold text-white tracking-tight">NSE Delivery Tracker</h1>
                <p class="text-sm text-slate-400 mt-1">Nifty 50 Deliverable Quantity & Price Action Overview</p>
            </div>
            <div>
                <span class="text-xs bg-slate-800 text-slate-300 border border-slate-700 px-3 py-1.5 rounded-full inline-block font-semibold">
                    Dashboard Updated: {last_updated} (IST)
                </span>
            </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-8">
            <div>
                <h2 class="text-sm font-bold text-slate-400 tracking-wider uppercase mb-3 flex items-center gap-1.5">
                    <span class="h-2 w-2 rounded-full bg-emerald-500"></span> Top 4 Market Gainers (Nifty 50)
                </h2>
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">{gainers_html}</div>
            </div>
            <div>
                <h2 class="text-sm font-bold text-slate-400 tracking-wider uppercase mb-3 flex items-center gap-1.5">
                    <span class="h-2 w-2 rounded-full bg-rose-500"></span> Top 4 Market Losers (Nifty 50)
                </h2>
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">{losers_html}</div>
            </div>
        </div>

        <div class="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
            <div class="p-5 border-b border-slate-800 bg-slate-900/50 flex flex-col sm:flex-row justify-between items-center gap-4">
                <input type="text" id="searchInput" placeholder="Search stock symbol..." 
                    class="w-full sm:w-72 bg-slate-800 text-sm text-slate-100 placeholder-slate-500 border border-slate-700 rounded-lg px-4 py-2 focus:outline-none focus:border-slate-500">
                <div class="text-xs text-slate-400">* Click sub-headers to sort columns</div>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-sm text-left border-collapse" id="dashboardTable">
                    <thead class="bg-slate-800 text-slate-300 border-b border-slate-700">
                        {header_row_1}
                        {header_row_2}
                    </thead>
                    <tbody>{rows_html}</tbody>
                </table>
            </div>
        </div>
    </div>
    <script>
        document.getElementById('searchInput').addEventListener('keyup', function() {{
            let filter = this.value.toUpperCase();
            let rows = document.getElementById('dashboardTable').getElementsByTagName('tr');
            // Skip first 2 header rows
            for (let i = 2; i < rows.length; i++) {{
                let symbolCell = rows[i].getElementsByTagName('td')[0];
                if (symbolCell) {{
                    let txtValue = symbolCell.textContent || symbolCell.innerText;
                    rows[i].style.display = txtValue.toUpperCase().indexOf(filter) > -1 ? "" : "none";
                }}
            }}
        }});

        let currentSortDir = {{}};
        function sortTable(columnIndex) {{
            const table = document.getElementById("dashboardTable");
            let rows = Array.from(table.rows).slice(2); // Skip both header rows
            let dir = currentSortDir[columnIndex] === 'asc' ? 'desc' : 'asc';
            currentSortDir = {{}};
            currentSortDir[columnIndex] = dir;

            rows.sort((rowA, rowB) => {{
                let cellA = rowA.getElementsByTagName("TD")[columnIndex];
                let cellB = rowB.getElementsByTagName("TD")[columnIndex];
                let valA = cellA.getAttribute("data-sort") || cellA.textContent.trim();
                let valB = cellB.getAttribute("data-sort") || cellB.textContent.trim();
                let floatA = parseFloat(valA.replace(/[%₹,]/g, ''));
                let floatB = parseFloat(valB.replace(/[%₹,]/g, ''));

                if (!isNaN(floatA) && !isNaN(floatB)) {{
                    return dir === 'asc' ? floatA - floatB : floatB - floatA;
                }} else {{
                    return dir === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
                }}
            }});
            const tbody = table.getElementsByTagName('tbody')[0];
            tbody.innerHTML = "";
            rows.forEach(row => tbody.appendChild(row));
        }}
    </script>
</body>
</html>"""

def generate_email_body_html(valid_filenames, gainers, losers, pages_url):
    last_updated = datetime.now().strftime('%d-%b-%Y %I:%M %p')
    
    table_rows = ""
    for i in range(4):
        g_sym = gainers[i]['SYMBOL'] if i < len(gainers) else "—"
        g_chg = f"+{gainers[i]['PCT_CHANGE']:.2f}%" if i < len(gainers) else "—"
        g_color = "#34d399" if i < len(gainers) else "#94a3b8"
        
        l_sym = losers[i]['SYMBOL'] if i < len(losers) else "—"
        l_chg = f"{losers[i]['PCT_CHANGE']:.2f}%" if i < len(losers) else "—"
        l_color = "#f87171" if i < len(losers) else "#94a3b8"
        
        table_rows += f"""
        <tr style="border-bottom: 1px solid #334155;">
            <td style="padding: 10px; text-align: left; font-weight: bold; color: #f1f5f9; font-size: 14px;">{g_sym}</td>
            <td style="padding: 10px; text-align: right; font-weight: bold; color: {g_color}; font-size: 14px;">{g_chg}</td>
            <td style="padding: 10px; text-align: left; font-weight: bold; color: #f1f5f9; font-size: 14px;">{l_sym}</td>
            <td style="padding: 10px; text-align: right; font-weight: bold; color: {l_color}; font-size: 14px;">{l_chg}</td>
        </tr>
        """
        
    file_list_items = ""
    for filename in valid_filenames[:5]:
        match = re.search(r'MTO_(\d{8})\.DAT', filename)
        date_label = datetime.strptime(match.group(1), '%d%m%Y').strftime('%d-%b-%Y') if match else ""
        file_list_items += f"""
        <li style="margin-bottom: 6px; font-size: 13px;">
            <code style="background-color: #1e293b; padding: 3px 6px; border-radius: 4px; color: #38bdf8; font-family: monospace; font-size: 13px;">{filename}</code> 
            <span style="color: #94a3b8; margin-left: 8px;">({date_label})</span>
        </li>
        """

    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0f172a; color: #f1f5f9; padding: 32px 24px; max-width: 600px; margin: 0 auto; border-radius: 12px; border: 1px solid #1e293b;">
        <h2 style="color: #ffffff; margin-top: 0; margin-bottom: 8px; font-size: 22px; font-weight: 800;">
            📊 NSE Delivery & Price Dashboard
        </h2>
        <p style="color: #94a3b8; font-size: 14px; margin-top: 0; margin-bottom: 24px; line-height: 1.5;">
            The daily sync run has successfully verified and formatted the <strong>7 most recent trading sessions</strong>.
        </p>
        
        <div style="margin-bottom: 32px; margin-top: 10px;">
            <a href="{pages_url}" style="display: inline-block; background-color: #2563eb; color: #ffffff; text-decoration: none; padding: 12px 24px; font-weight: bold; border-radius: 6px; font-size: 14px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                🔗 Click here to open your Live Dashboard
            </a>
        </div>
        
        <h3 style="color: #ffffff; margin-bottom: 12px; font-size: 16px; font-weight: 700; border-bottom: 1px solid #1e293b; padding-bottom: 6px;">
            🚀 Today's Market Leaders (Nifty 50)
        </h3>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 32px;">
            <thead>
                <tr style="background-color: #1e293b; color: #94a3b8; font-size: 12px; text-transform: uppercase;">
                    <th style="padding: 10px; text-align: left; font-weight: 600;">Top 4 Gainers</th>
                    <th style="padding: 10px; text-align: right; font-weight: 600;">% Change</th>
                    <th style="padding: 10px; text-align: left; font-weight: 600;">Top 4 Losers</th>
                    <th style="padding: 10px; text-align: right; font-weight: 600;">% Change</th>
                </tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>
        
        <h3 style="color: #ffffff; margin-bottom: 12px; font-size: 16px; font-weight: 700; border-bottom: 1px solid #1e293b; padding-bottom: 6px;">
            📁 Synchronized Report Files (Last 5 Sessions)
        </h3>
        <ul style="padding-left: 0; list-style-type: none; margin-top: 0; margin-bottom: 0;">
            {file_list_items}
        </ul>
        
        <hr style="border: 0; border-top: 1px solid #334155; margin: 32px 0;">
        <p style="color: #64748b; font-size: 11px; text-align: center; margin: 0; line-height: 1.4;">
            This is an automated operational notification. Compiled Excel spreadsheet is attached. <br>
            Dashboard generation timestamp: {last_updated} IST.
        </p>
    </div>
    """

def write_to_excel_workbook(master_df, valid_filenames, dates, chronological_indices):
    """Compiles ONLY the master Dashboard calculation dataframe into Sheet1 to keep file size lightweight."""
    print(f"Compiling calculated records into lightweight '{EXCEL_FILE}'...")
    
    with pd.ExcelWriter(EXCEL_FILE, engine='openpyxl') as writer:
        # Write ONLY the master Dashboard calculations
        master_df.to_excel(writer, sheet_name="Dashboard", index=False)

    # Apply custom number formats and widths to Dashboard sheet
    wb = load_workbook(EXCEL_FILE)
    ws = wb["Dashboard"]
    
    # Auto-adjust column widths
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    # Apply styling & number formatting
    # Col index 1 = Symbol
    # Cols 2 to 6 (B to F) = Price
    # Cols 7 to 11 (G to K) = Del%
    # Cols 12 to 16 (L to P) = Del Qty
    for r in range(2, len(TARGET_SYMBOLS) + 2):
        # 1. Close Prices: Cols 2 to 6 (B to F)
        for col in range(2, 7):
            ws.cell(row=r, column=col).number_format = '₹#,##0.00'
            
        # 2. Delivery Percentages: Cols 7 to 11 (G to K)
        for col in range(7, 12):
            ws.cell(row=r, column=col).number_format = '0.00%'
            val = ws.cell(row=r, column=col).value
            if isinstance(val, (int, float)):
                ws.cell(row=r, column=col, value=val / 100.0)
                
        # 3. Delivery Quantities: Cols 12 to 16 (L to P)
        for col in range(12, 17):
            ws.cell(row=r, column=col).number_format = '#,##0'

        # Format comparison columns which start right after the daily columns (starting at Col 17)
        # 4. Diff % Yesterday (Q / Col 17) & Diff % Avg4 (R / Col 18)
        for col in [17, 18]:
            ws.cell(row=r, column=col).number_format = '+0.00%;-0.00%;0.00%'
            val = ws.cell(row=r, column=col).value
            if isinstance(val, (int, float)):
                ws.cell(row=r, column=col, value=val / 100.0)

        # 5. Diff Qty Yesterday (S / Col 19) & Diff Qty Avg4 (T / Col 20)
        for col in [19, 20]:
            ws.cell(row=r, column=col).number_format = '+#,##0;-#,##0;0'

        # 6. Price Change: Absolute value difference (U / Col 21)
        ws.cell(row=r, column=21).number_format = '+₹#,##0.00;-₹#,##0.00;₹0.00'

    wb.save(EXCEL_FILE)
    print("Lightweight Excel workbook saved and styled.")

def send_email_dashboard(recipient, html_content, email_body_html):
    """Sends the formatted email with the live hosted button and attaches the final calculation Excel file."""
    smtp_server = os.environ.get('SMTP_SERVER')
    smtp_port = os.environ.get('SMTP_PORT', '587')
    smtp_user = os.environ.get('SMTP_USER')
    smtp_pass = os.environ.get('SMTP_PASSWORD')
    
    if not all([smtp_server, smtp_user, smtp_pass]):
        print("SMTP credentials are not fully configured. Email dispatch skipped.")
        return

    msg = MIMEMultipart('mixed')
    msg['Subject'] = f"NSE Delivery & Price Dashboard - {datetime.now().strftime('%d-%b-%Y')}"
    
    from email.utils import formataddr
    msg['From'] = formataddr(("NSE Dashboard", smtp_user))
    msg['To'] = recipient

    msg_alternative = MIMEMultipart('alternative')
    msg.attach(msg_alternative)
    
    part_html = MIMEText(email_body_html, 'html')
    msg_alternative.attach(part_html)

    # Attach the Compiled Excel Worksheet
    if os.path.exists(EXCEL_FILE):
        part_file = MIMEBase('application', 'octet-stream')
        try:
            with open(EXCEL_FILE, 'rb') as f:
                part_file.set_payload(f.read())
            encoders.encode_base64(part_file)
            part_file.add_header('Content-Disposition', f'attachment; filename="{EXCEL_FILE}"')
            msg.attach(part_file)
            print(f"Attached lightweight calculation worksheet: '{EXCEL_FILE}'")
        except Exception as e:
            print(f"Error attaching calculation worksheet: {e}")

    try:
        server = smtplib.SMTP(smtp_server, int(smtp_port))
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipient, msg.as_string())
        server.quit()
        print(f"Email successfully dispatched to {recipient}")
    except Exception as e:
        print(f"Failed to send email: {e}")

def write_github_summary(valid_filenames, gainers, losers):
    summary_file = os.environ.get('GITHUB_STEP_SUMMARY')
    if not summary_file:
        return
        
    repo = os.environ.get('GITHUB_REPOSITORY', 'username/repo')
    owner, repo_name = repo.split('/')
    pages_url = f"https://{owner}.github.io/{repo_name}/"
    
    with open(summary_file, 'w', encoding='utf-8') as f:
        f.write("# 📊 NSE Delivery & Price Dashboard\n\n")
        f.write("The daily sync run has successfully verified the **7 most recent trading sessions**.\n\n")
        f.write(f"### 🔗 **[Click here to open your Live Dashboard]({pages_url})**\n\n")
        
        f.write("## 🚀 Today's Market Leaders (Nifty 50)\n\n")
        f.write("| Top 4 Gainers | % Change | Top 4 Losers | % Change |\n")
        f.write("| --- | --- | --- | --- |\n")
        
        for i in range(4):
            g_sym = gainers[i]['SYMBOL'] if i < len(gainers) else "—"
            g_chg = f"+{gainers[i]['PCT_CHANGE']:.2f}%" if i < len(gainers) else "—"
            l_sym = losers[i]['SYMBOL'] if i < len(losers) else "—"
            l_chg = f"{losers[i]['PCT_CHANGE']:.2f}%" if i < len(losers) else "—"
            f.write(f"| **{g_sym}** | {g_chg} | **{l_sym}** | {l_chg} |\n")

def main():
    # 1. Sync & track files
    valid_filenames = sync_reports()
    
    # 2. Build master dashboard calculations
    master_df, chronological_indices, dates, gainers, losers = build_master_dashboard_data(valid_filenames)
    
    # 3. Compile Lightweight Master Excel spreadsheet (Dashboard sheet only)
    write_to_excel_workbook(master_df, valid_filenames, dates, chronological_indices)
    
    # 4. Generate local index.html page
    html_content = generate_html_content(master_df, dates, chronological_indices, gainers, losers)
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    print("Dashboard local file written successfully.")

    # 5. Write GTH Summary
    write_github_summary(valid_filenames, gainers, losers)

    # 6. Dispatch email containing clean summary body and lightweight Excel attachment
    recipient = get_recipient_email()
    if recipient:
        repo = os.environ.get('GITHUB_REPOSITORY', 'username/repo')
        owner, repo_name = repo.split('/')
        pages_url = f"https://{owner}.github.io/{repo_name}/"
        
        email_body_html = generate_email_body_html(valid_filenames, gainers, losers, pages_url)
        send_email_dashboard(recipient, html_content, email_body_html)
    else:
        print("No target recipient found in email.txt.")

if __name__ == '__main__':
    main()
