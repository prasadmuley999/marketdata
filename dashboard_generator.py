import os
import re
import glob
import pandas as pd
import yfinance as yf
from datetime import datetime

# 1. Standard Nifty 50 Tickers as of 2026
NIFTY_50_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", 
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BPCL", "BHARTIARTL", 
    "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB", "DRREDDY", 
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", 
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "ITC", 
    "INDUSINDBK", "INFY", "JSWSTEEL", "KOTAKBANK", "LT", 
    "LTIM", "M&M", "MARUTI", "NTPC", "NESTLEIND", "ONGC", 
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN", 
    "SUNPHARMA", "TCS", "TATACONSUM", "TATAMOTORS", "TATASTEEL", 
    "TECHM", "TITAN", "ULTRACEMCO", "WIPRO"
]

def get_latest_5_files():
    """Finds and parses the 5 most recent MTO DAT files chronologically by filename date."""
    files = glob.glob('reports/MTO_*.DAT')
    file_dates = []
    for f in files:
        match = re.search(r'MTO_(\d{8})\.DAT', f)
        if match:
            date_str = match.group(1)
            try:
                dt = datetime.strptime(date_str, '%d%m%Y')
                file_dates.append((dt, f))
            except ValueError:
                pass
    # Sort chronologically, newest first
    file_dates.sort(key=lambda x: x[0], reverse=True)
    return [f for dt, f in file_dates[:5]]

def parse_mto_file(filepath):
    """Parses NSE MTO .DAT file structure, resilient to header variance."""
    rows = []
    if not os.path.exists(filepath):
        return pd.DataFrame(columns=['SYMBOL', 'DEL_PCT'])
    
    with open(filepath, 'r') as f:
        for line in f:
            parts = [p.strip() for p in line.split(',')]
            # Data records in NSE DAT files start with record indicator '20'
            if len(parts) >= 7 and parts[0] == '20':
                symbol = parts[2]
                series = parts[3]
                try:
                    del_pct = float(parts[6])
                except ValueError:
                    del_pct = None
                rows.append({'SYMBOL': symbol, 'SERIES': series, 'DEL_PCT': del_pct})
                
    df = pd.DataFrame(rows)
    if not df.empty:
        # Filter for standard Compulsory Rolling Settlement (EQ)
        df = df[df['SERIES'] == 'EQ']
    else:
        df = pd.DataFrame(columns=['SYMBOL', 'DEL_PCT'])
    return df

def get_nifty_price_data():
    """Fetches stock pricing from Yahoo Finance to find the Top 4 Gainers and Losers."""
    tickers = [f"{sym}.NS" for sym in NIFTY_50_SYMBOLS]
    print("Fetching Nifty 50 market prices...")
    
    try:
        # Download 5 days to ensure we bypass holidays/weekends for price change reference
        df_price = yf.download(tickers, period="5d", progress=False)
        
        # Handle structural MultiIndex in yfinance download formatting
        close_df = df_price['Close'] if 'Close' in df_price else df_price
        
        last_row = close_df.iloc[-1]
        prev_row = close_df.iloc[-2]
        
        pct_change = ((last_row - prev_row) / prev_row) * 100
        
        # Clean ticker suffix (.NS) for alignment with standard MTO symbols
        pct_change.index = [t.replace('.NS', '') for t in pct_change.index]
        last_prices = last_row.copy()
        last_prices.index = [t.replace('.NS', '') for t in last_prices.index]
        
        price_summary = pd.DataFrame({
            'SYMBOL': pct_change.index,
            'PRICE': last_prices.values,
            'PCT_CHANGE': pct_change.values
        }).dropna()
        
        gainers = price_summary.sort_values(by='PCT_CHANGE', ascending=False).head(4).to_dict('records')
        losers = price_summary.sort_values(by='PCT_CHANGE', ascending=True).head(4).to_dict('records')
        return gainers, losers
    except Exception as e:
        print(f"Error fetching live pricing data: {e}")
        return [], []

def build_dashboard():
    latest_files = get_latest_5_files()
    if not latest_files:
        print("No MTO files found in 'reports/' directory. Run the downloader first.")
        return

    # Master structure containing all target Nifty 50 components
    master_df = pd.DataFrame({'SYMBOL': NIFTY_50_SYMBOLS})
    col_names = []
    
    # Merge delivery columns from up to 5 parsed historical files
    for i, file_path in enumerate(latest_files):
        match = re.search(r'MTO_(\d{8})\.DAT', file_path)
        if match:
            dt = datetime.strptime(match.group(1), '%d%m%Y')
            formatted_date = dt.strftime('%d-%b')
        else:
            formatted_date = f"D-{i}"
            
        col_name = "Del% Today" if i == 0 else f"Del% {formatted_date}"
        col_names.append(col_name)
        
        df_mto = parse_mto_file(file_path)
        df_mto = df_mto.rename(columns={'DEL_PCT': col_name})
        master_df = pd.merge(master_df, df_mto[['SYMBOL', col_name]], on='SYMBOL', how='left')

    # Fill any completely missing dates with standard placeholder
    for col in col_names:
        if col not in master_df.columns:
            master_df[col] = None

    # Sort remaining active column slots chronologically (oldest to newest relative to D-0)
    del_today = "Del% Today"
    del_historical = [c for c in col_names if c != del_today]

    # Calculate Custom Math Columns
    if del_today in master_df.columns and len(del_historical) > 0:
        prev_day_col = del_historical[0] # Immediately prior day (D-1)
        master_df['Diff vs D-1'] = master_df[del_today] - master_df[prev_day_col]
        master_df['Diff vs Avg4'] = master_df[del_today] - master_df[del_historical[:4]].mean(axis=1)
    else:
        master_df['Diff vs D-1'] = None
        master_df['Diff vs Avg4'] = None

    # Default Sorting matches original sheet layout: Diff vs D-1 Descending
    master_df = master_df.sort_values(by='Diff vs D-1', ascending=False, na_position='last')

    # Get Top Price Gainers/Losers
    gainers, losers = get_nifty_price_data()
    
    # Generate the complete Interactive HTML File
    generate_html(master_df, del_today, del_historical, gainers, losers)

def generate_html(df, today_col, historical_cols, gainers, losers):
    # Setup Dynamic Table Column Headers
    header_cols_html = f'<th onclick="sortTable(0)" class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider cursor-pointer hover:bg-slate-700">Symbol</th>\n'
    header_cols_html += f'<th onclick="sortTable(1)" class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider cursor-pointer hover:bg-slate-700">{today_col}</th>\n'
    
    col_idx = 2
    for h_col in historical_cols[:4]:
        header_cols_html += f'<th onclick="sortTable({col_idx})" class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider cursor-pointer hover:bg-slate-700">{h_col}</th>\n'
        col_idx += 1
        
    header_cols_html += f'<th onclick="sortTable({col_idx})" class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider cursor-pointer hover:bg-slate-700">Diff vs D-1</th>\n'
    header_cols_html += f'<th onclick="sortTable({col_idx+1})" class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider cursor-pointer hover:bg-slate-700">Diff vs Avg4</th>\n'

    # Construct individual Table Row markup
    rows_html = ""
    for _, row in df.iterrows():
        sym = row['SYMBOL']
        today_val = row[today_col]
        
        t_val_str = f"{today_val:.2f}" if pd.notna(today_val) else "—"
        rows_html += f'<tr class="border-b border-slate-700 hover:bg-slate-800 transition-colors">\n'
        rows_html += f'  <td class="px-4 py-3 text-sm font-bold text-slate-100">{sym}</td>\n'
        rows_html += f'  <td class="px-4 py-3 text-sm text-right text-slate-300" data-sort="{today_val if pd.notna(today_val) else -1}">{t_val_str}</td>\n'
        
        # Populate Historical Delivery Columns
        for h_col in historical_cols[:4]:
            h_val = row[h_col]
            h_val_str = f"{h_val:.2f}" if pd.notna(h_val) else "—"
            rows_html += f'  <td class="px-4 py-3 text-sm text-right text-slate-400" data-sort="{h_val if pd.notna(h_val) else -1}">{h_val_str}</td>\n'
            
        # Format Diff columns dynamically with custom red/green color tags
        diff_d1 = row['Diff vs D-1']
        diff_avg4 = row['Diff vs Avg4']
        
        for diff_val in [diff_d1, diff_avg4]:
            if pd.isna(diff_val):
                rows_html += f'  <td class="px-4 py-3 text-sm text-right text-slate-500" data-sort="-999">—</td>\n'
            else:
                color_class = "text-emerald-400 font-semibold" if diff_val > 0 else "text-rose-400 font-semibold" if diff_val < 0 else "text-slate-400"
                sign = "+" if diff_val > 0 else ""
                rows_html += f'  <td class="px-4 py-3 text-sm text-right {color_class}" data-sort="{diff_val}">{sign}{diff_val:.2f}%</td>\n'
        rows_html += f'</tr>\n'

    # Build Top Gainers and Losers visual blocks
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

    # Complete HTML Dashboard Output String
    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NSE Equity Delivery & Price Dashboard</title>
    <!-- Tailwind CSS CDN for styling -->
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body {{
            background-color: #0f172a;
        }}
    </style>
</head>
<body class="text-slate-100 font-sans min-h-screen">
    <div class="max-w-7xl mx-auto px-4 py-8">
        
        <!-- Header Section -->
        <div class="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-slate-700 pb-6 mb-8 gap-4">
            <div>
                <h1 class="text-3xl font-extrabold text-white tracking-tight">NSE Delivery Tracker</h1>
                <p class="text-sm text-slate-400 mt-1">Nifty 50 Deliverable Quantity & Price Action Overview</p>
            </div>
            <div class="text-left md:text-right">
                <span class="text-xs bg-slate-800 text-slate-300 border border-slate-700 px-3 py-1.5 rounded-full font-medium inline-block">
                    Dashboard Updated: {last_updated} (IST)
                </span>
            </div>
        </div>

        <!-- Top Performers Section -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-8">
            <!-- Gainers -->
            <div>
                <h2 class="text-sm font-bold text-slate-400 tracking-wider uppercase mb-3 flex items-center gap-1.5">
                    <span class="h-2 w-2 rounded-full bg-emerald-500"></span> Top 4 Market Gainers
                </h2>
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    {gainers_html if gainers else '<div class="col-span-4 text-slate-500 text-xs text-center py-4 bg-slate-850 rounded">No data available</div>'}
                </div>
            </div>
            <!-- Losers -->
            <div>
                <h2 class="text-sm font-bold text-slate-400 tracking-wider uppercase mb-3 flex items-center gap-1.5">
                    <span class="h-2 w-2 rounded-full bg-rose-500"></span> Top 4 Market Losers
                </h2>
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    {losers_html if losers else '<div class="col-span-4 text-slate-500 text-xs text-center py-4 bg-slate-850 rounded">No data available</div>'}
                </div>
            </div>
        </div>

        <!-- Main Tracker Table Container -->
        <div class="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
            <!-- Search & Filter Controls -->
            <div class="p-5 border-b border-slate-800 bg-slate-900/50 flex flex-col sm:flex-row justify-between items-center gap-4">
                <div class="relative w-full sm:w-72">
                    <input type="text" id="searchInput" placeholder="Search stock symbol..." 
                        class="w-full bg-slate-800 text-sm text-slate-100 placeholder-slate-500 border border-slate-700 rounded-lg px-4 py-2 focus:outline-none focus:border-slate-500 transition-colors">
                </div>
                <div class="text-xs text-slate-400">
                    * Click any column header to sort ascending / descending
                </div>
            </div>

            <!-- Scrollable Table -->
            <div class="overflow-x-auto">
                <table class="w-full text-sm text-left border-collapse" id="dashboardTable">
                    <thead class="bg-slate-800 text-slate-300 border-b border-slate-700">
                        <tr>
                            {header_cols_html}
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Client-side Interactive Functionality -->
    <script>
        // 1. Instantly Filter Table Rows on User Keyup
        document.getElementById('searchInput').addEventListener('keyup', function() {{
            let filter = this.value.toUpperCase();
            let rows = document.getElementById('dashboardTable').getElementsByTagName('tr');
            for (let i = 1; i < rows.length; i++) {{
                let symbolCell = rows[i].getElementsByTagName('td')[0];
                if (symbolCell) {{
                    let txtValue = symbolCell.textContent || symbolCell.innerText;
                    rows[i].style.display = txtValue.toUpperCase().indexOf(filter) > -1 ? "" : "none";
                }}
            }}
        }});

        // 2. Perform Clean Multi-Type Table Column Sorting
        let currentSortDir = {{}};
        function sortTable(columnIndex) {{
            const table = document.getElementById("dashboardTable");
            let rows = Array.from(table.rows).slice(1);
            let dir = currentSortDir[columnIndex] === 'asc' ? 'desc' : 'asc';
            currentSortDir = {{}}; // reset others
            currentSortDir[columnIndex] = dir;

            rows.sort((rowA, rowB) => {{
                let cellA = rowA.getElementsByTagName("TD")[columnIndex];
                let cellB = rowB.getElementsByTagName("TD")[columnIndex];

                let valA = cellA.getAttribute("data-sort") || cellA.textContent.trim();
                let valB = cellB.getAttribute("data-sort") || cellB.textContent.trim();

                let floatA = parseFloat(valA);
                let floatB = parseFloat(valB);

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

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html_template)
    print("Dashboard generated successfully: 'index.html'")

if __name__ == '__main__':
    build_dashboard()
