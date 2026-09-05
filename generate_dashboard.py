import os
import re
import glob
import urllib.request
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# Constants
NIFTY_50_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", 
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BPCL", "BHARTIARTL", 
    "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB", "DRREDDY", 
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", 
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "ITC", 
    "INDUSINDBK", "INFY", "JSWSTEEL", "KOTAKBANK", "LT", 
    "LTM", "M&M", "MARUTI", "NTPC", "NESTLEIND", "ONGC", 
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN", 
    "SUNPHARMA", "TCS", "TATACONSUM", "TMPV", "TATASTEEL", 
    "TECHM", "TITAN", "ULTRACEMCO", "WIPRO"
]

def is_valid_file(filepath):
    """Ensures the file is a valid DAT file, not an HTML error or empty response."""
    if not os.path.exists(filepath):
        return False
    if os.path.getsize(filepath) < 500: # Standard MTO files are much larger
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
    """Downloads a file with headers to prevent blocklisting and verifies validity."""
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
    """Ensures exactly 7 of the most recent market reports are available."""
    os.makedirs('reports', exist_ok=True)
    
    # Calculate IST current time from UTC
    utc_now = datetime.utcnow()
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    
    keep_files = set()
    count = 0
    current_date = ist_now
    checked_days = 0
    
    print("Checking database. Ensuring 7 most recent trading reports are present...")
    
    # Trace back up to 30 calendar days to find 7 valid trading sessions
    while count < 7 and checked_days < 30:
        checked_days += 1
        
        # Skip Saturday (5) and Sunday (6)
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
            print(f"File missing/invalid for {current_date.strftime('%Y-%m-%d')}. Attempting download...")
            if download_file(url, filepath):
                print(f"-> Successfully downloaded: {filename}")
                keep_files.add(filename)
                count += 1
            else:
                print(f"-> Not available (holiday, weekend, or not yet published)")
                
        current_date -= timedelta(days=1)
        
    # Remove older files beyond the 7 tracked ones
    all_files = glob.glob('reports/MTO_*.DAT')
    for f in all_files:
        basename = os.path.basename(f)
        if basename not in keep_files:
            try:
                os.remove(f)
                print(f"Removed older report file: {basename}")
            except Exception as e:
                print(f"Error clean-up: {basename}: {e}")
                
    return sorted(list(keep_files), reverse=True)

def parse_mto_file(filepath):
    rows = []
    if not os.path.exists(filepath):
        return pd.DataFrame(columns=['SYMBOL', 'DEL_PCT'])
    with open(filepath, 'r') as f:
        for line in f:
            parts = [p.strip() for p in line.split(',')]
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
        df = df[df['SERIES'] == 'EQ']
    else:
        df = pd.DataFrame(columns=['SYMBOL', 'DEL_PCT'])
    return df

def get_nifty_price_data():
    tickers = [f"{sym}.NS" for sym in NIFTY_50_SYMBOLS]
    try:
        df_price = yf.download(tickers, period="5d", progress=False)
        close_df = df_price['Close'] if 'Close' in df_price else df_price
        last_row = close_df.iloc[-1]
        prev_row = close_df.iloc[-2]
        pct_change = ((last_row - prev_row) / prev_row) * 100
        
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
        print(f"Error fetching market prices: {e}")
        return [], []

def write_github_summary(valid_filenames, gainers, losers):
    """Writes a clean report and dynamic dashboard links directly to the GitHub Actions page."""
    summary_file = os.environ.get('GITHUB_STEP_SUMMARY')
    if not summary_file:
        return
        
    # Extract owner and repo dynamically from GHA environment variables
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
            
        f.write("\n---\n\n")
        f.write("### 📂 Synchronized Report Files (Last 7 Sessions)\n")
        for filename in valid_filenames:
            match = re.search(r'MTO_(\d{8})\.DAT', filename)
            if match:
                dt = datetime.strptime(match.group(1), '%d%m%Y')
                f.write(f"- `{filename}` ({dt.strftime('%d-%b-%Y')})\n")
            else:
                f.write(f"- `{filename}`\n")

def build_dashboard(valid_filenames):
    master_df = pd.DataFrame({'SYMBOL': NIFTY_50_SYMBOLS})
    col_names = []
    
    for i, filename in enumerate(valid_filenames):
        match = re.search(r'MTO_(\d{8})\.DAT', filename)
        if match:
            dt = datetime.strptime(match.group(1), '%d%m%Y')
            formatted_date = dt.strftime('%d-%b')
        else:
            formatted_date = f"D-{i}"
            
        col_name = "Del% Today" if i == 0 else f"Del% {formatted_date}"
        col_names.append(col_name)
        
        df_mto = parse_mto_file(os.path.join('reports', filename))
        df_mto = df_mto.rename(columns={'DEL_PCT': col_name})
        master_df = pd.merge(master_df, df_mto[['SYMBOL', col_name]], on='SYMBOL', how='left')

    del_today = "Del% Today"
    del_historical = [c for c in col_names if c != del_today]

    if del_today in master_df.columns and len(del_historical) > 0:
        prev_day_col = del_historical[0]
        master_df['Diff vs D-1'] = master_df[del_today] - master_df[prev_day_col]
        master_df['Diff vs Avg4'] = master_df[del_today] - master_df[del_historical[:4]].mean(axis=1)
    else:
        master_df['Diff vs D-1'] = None
        master_df['Diff vs Avg4'] = None

    master_df = master_df.sort_values(by='Diff vs D-1', ascending=False, na_position='last')
    gainers, losers = get_nifty_price_data()
    
    # Return HTML content along with gainers and losers to pass to the summary builder
    html_content = generate_html_content(master_df, del_today, del_historical, gainers, losers)
    return html_content, gainers, losers

def generate_html_content(df, today_col, historical_cols, gainers, losers):
    header_cols_html = f'<th onclick="sortTable(0)" class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider cursor-pointer hover:bg-slate-700">Symbol</th>\n'
    header_cols_html += f'<th onclick="sortTable(1)" class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider cursor-pointer hover:bg-slate-700">{today_col}</th>\n'
    
    col_idx = 2
    for h_col in historical_cols[:4]:
        header_cols_html += f'<th onclick="sortTable({col_idx})" class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider cursor-pointer hover:bg-slate-700">{h_col}</th>\n'
        col_idx += 1
        
    header_cols_html += f'<th onclick="sortTable({col_idx})" class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider cursor-pointer hover:bg-slate-700">Diff vs D-1</th>\n'
    header_cols_html += f'<th onclick="sortTable({col_idx+1})" class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider cursor-pointer hover:bg-slate-700">Diff vs Avg4</th>\n'

    rows_html = ""
    for _, row in df.iterrows():
        sym = row['SYMBOL']
        today_val = row[today_col]
        t_val_str = f"{today_val:.2f}" if pd.notna(today_val) else "—"
        
        rows_html += f'<tr class="border-b border-slate-700 hover:bg-slate-800 transition-colors">\n'
        rows_html += f'  <td class="px-4 py-3 text-sm font-bold text-slate-100">{sym}</td>\n'
        rows_html += f'  <td class="px-4 py-3 text-sm text-right text-slate-300" data-sort="{today_val if pd.notna(today_val) else -1}">{t_val_str}</td>\n'
        
        for h_col in historical_cols[:4]:
            h_val = row[h_col]
            h_val_str = f"{h_val:.2f}" if pd.notna(h_val) else "—"
            rows_html += f'  <td class="px-4 py-3 text-sm text-right text-slate-400" data-sort="{h_val if pd.notna(h_val) else -1}">{h_val_str}</td>\n'
            
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
<body class="text-slate-100 font-sans min-h-screen">
    <div class="max-w-7xl mx-auto px-4 py-8">
        <div class="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-slate-700 pb-6 mb-8 gap-4">
            <div>
                <h1 class="text-3xl font-extrabold text-white tracking-tight">NSE Delivery Tracker</h1>
                <p class="text-sm text-slate-400 mt-1">Nifty 50 Deliverable Quantity & Price Action Overview</p>
            </div>
            <div>
                <span class="text-xs bg-slate-800 text-slate-300 border border-slate-700 px-3 py-1.5 rounded-full inline-block">
                    Dashboard Updated: {last_updated} (IST)
                </span>
            </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-8">
            <div>
                <h2 class="text-sm font-bold text-slate-400 tracking-wider uppercase mb-3 flex items-center gap-1.5">
                    <span class="h-2 w-2 rounded-full bg-emerald-500"></span> Top 4 Market Gainers
                </h2>
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">{gainers_html}</div>
            </div>
            <div>
                <h2 class="text-sm font-bold text-slate-400 tracking-wider uppercase mb-3 flex items-center gap-1.5">
                    <span class="h-2 w-2 rounded-full bg-rose-500"></span> Top 4 Market Losers
                </h2>
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">{losers_html}</div>
            </div>
        </div>

        <div class="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
            <div class="p-5 border-b border-slate-800 bg-slate-900/50 flex flex-col sm:flex-row justify-between items-center gap-4">
                <input type="text" id="searchInput" placeholder="Search stock symbol..." 
                    class="w-full sm:w-72 bg-slate-800 text-sm text-slate-100 placeholder-slate-500 border border-slate-700 rounded-lg px-4 py-2 focus:outline-none focus:border-slate-500">
                <div class="text-xs text-slate-400">* Click headers to sort</div>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-sm text-left border-collapse" id="dashboardTable">
                    <thead class="bg-slate-800 text-slate-300 border-b border-slate-700">
                        <tr>{header_cols_html}</tr>
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
            for (let i = 1; i < rows.length; i++) {{
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
            let rows = Array.from(table.rows).slice(1);
            let dir = currentSortDir[columnIndex] === 'asc' ? 'desc' : 'asc';
            currentSortDir = {{}};
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

def get_recipient_email():
    if os.path.exists('email.txt'):
        try:
            with open('email.txt', 'r') as f:
                return f.read().strip()
        except Exception:
            pass
    return None

def send_email_dashboard(recipient, html_content):
    """Dispatches the HTML dashboard to the specified recipient using SMTP."""
    smtp_server = os.environ.get('SMTP_SERVER')
    smtp_port = os.environ.get('SMTP_PORT', '587')
    smtp_user = os.environ.get('SMTP_USER')
    smtp_pass = os.environ.get('SMTP_PASSWORD')
    
    if not all([smtp_server, smtp_user, smtp_pass]):
        print("SMTP credentials are not fully configured. Email dispatch skipped.")
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"NSE Delivery & Price Dashboard - {datetime.now().strftime('%d-%b-%Y')}"
    msg['From'] = smtp_user
    msg['To'] = recipient

    # Add HTML as inline body
    part_html = MIMEText(html_content, 'html')
    msg.attach(part_html)

    # Attach file
    part_file = MIMEBase('application', 'octet-stream')
    part_file.set_payload(html_content.encode('utf-8'))
    encoders.encode_base64(part_file)
    part_file.add_header('Content-Disposition', 'attachment; filename="dashboard.html"')
    msg.attach(part_file)

    try:
        server = smtplib.SMTP(smtp_server, int(smtp_port))
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipient, msg.as_string())
        server.quit()
        print(f"Email successfully dispatched to {recipient}")
    except Exception as e:
        print(f"Failed to send email: {e}")


def main():
    # 1. Sync & track 7 files
    valid_filenames = sync_reports()
    
    # 2. Compile dashboard structure
    html_content, gainers, losers = build_dashboard(valid_filenames)
    
    # 3. Write locally to index.html (for GitHub Pages hosting)
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)

    # 4. Write Markdown Report to GitHub Actions Summary (Job Summary)
    write_github_summary(valid_filenames, gainers, losers)

    # 5. Send custom HTML email if email.txt and secrets exist
    recipient = get_recipient_email()
    if recipient:
        send_email_dashboard(recipient, html_content)
    else:
        print("No target recipient found in email.txt.")

if __name__ == '__main__':
    main()
