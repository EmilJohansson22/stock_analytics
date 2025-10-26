import panel as pn
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timedelta
from value_calculation import Stock

def get_value(metrics, method='summary', history=None):
    """
    metrics: dict of raw metrics (as returned by get_ticker_metrics)
    method: 'summary' | 'relative' | 'dcf' | 'gordon'
    history: optional history (pandas.DataFrame) to pass to Stock
    Returns: dict (valuation results) or None on error
    """
    if not metrics or not isinstance(metrics, dict):
        return None
    try:
        # try common ticker keys
        ticker = metrics.get('Ticker') or metrics.get('ticker') or metrics.get('Symbol') or ''
        price = metrics.get('Price') or metrics.get('price')
        st = Stock(ticker, metrics=metrics, history=history, price=price)
        if method == 'summary':
            return st.summary()
        if method == 'relative':
            return st.get_relative_value()
        if method == 'dcf':
            return st.get_dcf()
        if method == 'gordon':
            return st.get_growth_dividend_valuation()
        return None
    except Exception:
        return None

def merge_valuations_into_metrics(metrics, valuations, prefix='Val_'):
    """
    Returns a shallow copy of metrics with valuation items added using a prefix.
    Does not mutate the input dict.
    """
    if not isinstance(metrics, dict):
        metrics = {}
    out = dict(metrics)
    if valuations:
        for k, v in valuations.items():
            out[f"{prefix}{k}"] = v
    return out

# Load Panel extensions
pn.extension('plotly', sizing_mode="stretch_width")

# --- Copied Function from yfinance_ttm_metrics.py ---
# This is included so the file is self-contained.

def get_ticker_metrics(ticker_symbol):
    """
    Fetches a specific set of TTM financial metrics for a given stock ticker
    using the yfinance library.
    """
    try:
        # Create a Ticker object
        ticker = yf.Ticker(ticker_symbol)

        # .info contains a lot of summary data (P/E, P/S, Shares, etc.)
        info = ticker.info
        
        # Get price from history
        hist = ticker.history(period="1d")
        if hist.empty:
             print(f"Error: Could not retrieve history (price) data for {ticker_symbol}. Symbol may be delisted.")
             # We can still try to get metrics, but will use 'info' for price
             price = info.get('currentPrice', info.get('previousClose'))
             if price is None:
                 return None # Give up if no price is found
        else:
            price = hist['Close'].iloc[-1]
        
        # Check if info dictionary is usable
        if not info or info.get('sharesOutstanding') is None:
            print(f"Warning: No summary info found for {ticker_symbol}. Some metrics (P/E, P/S, Shares) will be missing.")
            if not info:
                info = {}
        
        # We need quarterly data to calculate TTM
        q_is = ticker.quarterly_income_stmt
        q_cf = ticker.quarterly_cashflow
        q_bs = ticker.quarterly_balance_sheet
        
        # --- Check if financial data is available ---
        if q_is.empty or q_cf.empty or q_bs.empty:
            print(f"Error: Could not retrieve financial statements for {ticker_symbol}. It might be a fund (like VOO), delisted, or data is unavailable.")
            # For funds, we can still return basic info
            if info.get('navPrice'):
                 return {
                    'Ticker': ticker_symbol,
                    'Price': price,
                    'Currency': info.get('currency', 'N/A'),
                    'Shares_Outstanding': info.get('sharesOutstanding'),
                    'NAV': info.get('navPrice', "N/A"),
                    'P_E_TTM': info.get('trailingPE'),
                 }
            return None
            
        # --- Helper logic for TTM and Latest Data ---
        ttm_is = q_is.iloc[:, :4].sum(axis=1)
        ttm_cf = q_cf.iloc[:, :4].sum(axis=1)
        latest_bs = q_bs.iloc[:, 0]
        
        # --- Initialize results dictionary ---
        metrics = {}
        # Try to get pre-calculated EV first
        # --- Populate Metrics ---
        metrics['Ticker'] = ticker_symbol
        metrics['Price'] = price
        metrics['Currency'] = info.get('currency', 'N/A')
        metrics['Shares_Outstanding'] = info.get('sharesOutstanding')
        metrics['Market_Cap'] = info.get('marketCap')
        ev = info.get('enterpriseValue')
        if not ev:
            # Try to calculate it: EV = Market Cap + Total Debt - Total Cash
            market_cap = info.get('marketCap')
            total_debt = latest_bs.get('Total Debt')
            total_cash = latest_bs.get('Cash And Cash Equivalents')
            
            # Check if we have all components (they must not be None)
            if market_cap is not None and total_debt is not None and total_cash is not None:
                ev = market_cap + total_debt - total_cash
            else:
                ev = None # Not enough data to calculate
                
        metrics['Enterprise_Value'] = ev
        metrics['Total_Debt'] = latest_bs.get('Total Debt')
        metrics['Total_Cash'] = latest_bs.get('Cash And Cash Equivalents')
        metrics['Total_Assets'] = latest_bs.get('Total Assets')
        metrics['Revenue_TTM'] = ttm_is.get('Total Revenue')
        metrics['COGS_TTM'] = ttm_is.get('Cost Of Revenue')
        metrics['Operating_Expenses_TTM'] = ttm_is.get('Operating Expense')
        metrics['EBIT_TTM'] = ttm_is.get('Operating Income')
        metrics['EBITDA_TTM'] = ttm_is.get('EBITDA')
        metrics['Net_Income_TTM'] = ttm_is.get('Net Income')
        metrics['Depreciation_Amortization_TTM'] = ttm_cf.get('Depreciation And Amortization')
        metrics['Capital_Expenditures_TTM'] = ttm_cf.get('Capital Expenditure')
        metrics['Change_in_Working_Capital_TTM'] = ttm_cf.get('Change In Working Capital')

        ebt_ttm = ttm_is.get('Pretax Income')
        tax_ttm = ttm_is.get('Tax Provision')
        if ebt_ttm and tax_ttm and ebt_ttm != 0:
            metrics['Tax_Rate_TTM'] = tax_ttm / ebt_ttm
        else:
            metrics['Tax_Rate_TTM'] = None

        metrics['P_E_TTM'] = info.get('trailingPE')
        metrics['P_S_TTM'] = info.get('trailingPS')
        metrics['P_B'] = info.get('priceToBook')
        metrics['PEG'] = info.get('pegRatio')
        
        metrics['NAV'] = info.get('navPrice', None)
        # metrics['Sector_Growth_Rate'] = "N/A (Not provided by yfinance)"
        # metrics['Total_Addressable_Market'] = "N/A (Not provided by yfinance)"

        return metrics
    except Exception as e:
        print(f"Error in get_ticker_metrics for {ticker_symbol}: {e}")
        return None

# --- New Function to Get Stock History ---

def get_stock_history(ticker_symbol):
    """
    Fetches the last 12 months of stock price data.
    """
    try:
        ticker = yf.Ticker(ticker_symbol)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)
        hist = ticker.history(start=start_date, end=end_date)
        return hist
    except Exception as e:
        print(f"Error fetching history for {ticker_symbol}: {e}")
        return pd.DataFrame() # Return empty DataFrame on error

# --- Build the Panel App ---

# 1. Define Widgets
ticker_input = pn.widgets.TextInput(name='Ticker', value='GOOG', placeholder='Enter ticker...')
fetch_button = pn.widgets.Button(name='Fetch Data', button_type='primary')

# --- NEW: Valuation controls (non-invasive) ---
valuation_method = pn.widgets.Select(name='Valuation Method',
                                     options=['summary', 'relative', 'dcf', 'gordon'],
                                     value='summary')
dcf_years = pn.widgets.IntInput(name='DCF Years', value=5, start=1)
dcf_growth = pn.widgets.FloatInput(name='Projection Growth', value=0.03, step=0.01)
dcf_discount = pn.widgets.FloatInput(name='Discount Rate', value=0.10, step=0.01)
dcf_terminal_growth = pn.widgets.FloatInput(name='Terminal Growth', value=0.02, step=0.01)
dcf_terminal_multiple = pn.widgets.FloatInput(name='Terminal Multiple (optional)', value=None)

# 2. Define Output Panes (as placeholders)
status_text = pn.pane.Markdown("")
metrics_table = pn.pane.DataFrame(None, width=600, height=400)
stock_plot = pn.pane.Plotly(None, min_height=400)

# --- NEW: Valuation output pane ---
valuation_pane = pn.pane.DataFrame(None, width=300, height=400)

# 3. Define the Core "Update" Function
def update_dashboard(event):
    # Get ticker and set loading state
    ticker = ticker_input.value.upper().strip()
    fetch_button.loading = True
    status_text.object = f"### Fetching data for {ticker}..."
    
    # Clear old data
    metrics_table.object = None
    stock_plot.object = None
    valuation_pane.object = None

    try:
        # --- Fetch Data ---
        metrics_data = get_ticker_metrics(ticker)
        hist_data = get_stock_history(ticker)

        # --- Handle Errors ---
        if metrics_data is None or hist_data.empty:
            status_text.object = f"### <font color='red'>Error: Could not fetch all data for {ticker}.</font>"
            fetch_button.loading = False
            return

        # --- 1. Update Metrics Table ---
        # Convert metrics dict to a DataFrame for display
        metrics_df = pd.DataFrame.from_dict(metrics_data, orient='index', columns=['Value'])
        metrics_df.index.name = 'Metric'
        metrics_table.object = metrics_df

        # --- 1b. Compute Valuation (non-invasive logic) ---
        vals = None
        try:
            method = valuation_method.value or 'summary'
            # Use Stock directly for DCF so we can pass parameters live
            if method == 'dcf':
                st = Stock(ticker, metrics=metrics_data, history=hist_data, price=metrics_data.get('Price'))
                tm = None if dcf_terminal_multiple.value in (None, '') else float(dcf_terminal_multiple.value)
                vals = st.get_dcf(years=int(dcf_years.value),
                                  growth=float(dcf_growth.value),
                                  discount=float(dcf_discount.value),
                                  terminal_growth=float(dcf_terminal_growth.value),
                                  terminal_multiple=tm)
            else:
                # use existing helper for summary/relative/gordon
                vals = get_value(metrics_data, method=method, history=hist_data)
        except Exception as e:
            print(f"Valuation computation error: {e}")
            vals = None

        # Append valuation rows to the metrics table (prefixed) and show valuation pane
        if vals:
            # merge into DataFrame copy to avoid mutating original metrics_data structure
            try:
                for k, v in vals.items():
                    metrics_df.loc[f'Val_{k}'] = [v]
                metrics_table.object = metrics_df
                # show valuation results separately too
                val_df = pd.DataFrame.from_dict(vals, orient='index', columns=['Value'])
                val_df.index.name = 'Valuation'
                valuation_pane.object = val_df
            except Exception:
                pass

        # --- 2. Update Plotly Chart ---
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=hist_data.index, 
            y=hist_data['Close'], 
            mode='lines',
            name='Close Price'
        ))
        fig.update_layout(
            title=f'{ticker} Stock Price (Last 12 Months)',
            xaxis_title='Date',
            yaxis_title=f"Price ({metrics_data.get('Currency', 'USD')})",
            hovermode='x unified'
        )
        stock_plot.object = fig
        
        status_text.object = f"### {ticker} Dashboard"

    except Exception as e:
        status_text.object = f"### <font color='red'>An error occurred: {e}</font>"
    
    # Done loading
    fetch_button.loading = False

# 4. Link Widgets to the Update Function
fetch_button.on_click(update_dashboard)

# 5. Define the Layout (update to include valuation controls/pane)
inputs = pn.Row(ticker_input, fetch_button, valuation_method, dcf_years, dcf_growth, dcf_discount, dcf_terminal_growth, dcf_terminal_multiple, align="center")

dashboard_layout = pn.Column(
    pn.pane.Markdown("# Stock TTM Dashboard", align="center"),
    inputs,
    pn.layout.Divider(),
    status_text,
    pn.Row(metrics_table, stock_plot, valuation_pane) # Display table, plot and valuation pane side-by-side
)

# 6. Make the app servable
dashboard_layout.servable()
