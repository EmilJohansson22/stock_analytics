import re

class Stock:
    """
    Lightweight valuation helper that accepts a ticker, a metrics dict (variable shape),
    and optional price/history. Methods return dicts with computed values or None if data missing.
    This version also attempts to derive missing inputs from available ones (e.g. market_cap from shares*price,
    revenue from market_cap/ps, net_income from market_cap/pe, EV from market_cap+debt-cash, etc.).
    """
    def __init__(self, ticker, metrics=None, history=None, price=None):
        self.ticker = ticker
        self.raw = metrics or {}
        self.history = history
        self._norm = self._normalize_keys(self.raw)
        self.price = price if price is not None else self._extract_price()

        # primary inputs (attempt to parse numbers from normalized dict)
        self.shares = self._get_number('shares_outstanding')
        self.market_cap = self._get_number('market_cap')
        self.enterprise_value = self._get_number('enterprise_value')
        self.revenue_ttm = self._get_number('revenue_ttm')
        self.ebit_ttm = self._get_number('ebit_ttm') or self._get_number('operating_income_ttm')
        self.net_income_ttm = self._get_number('net_income_ttm')
        self.total_debt = self._get_number('total_debt')
        self.total_cash = self._get_number('total_cash')
        self.capex_ttm = self._get_number('capital_expenditures_ttm')
        self.depr_ttm = self._get_number('depreciation_amortization_ttm')
        self.change_wc = self._get_number('change_in_working_capital_ttm')
        self.tax_rate = self._get_number('tax_rate_ttm') or self._infer_tax_rate()
        self.price_to_book = self._get_number('p_b') or self._get_number('price_to_book')
        self.pe = self._get_number('p_e_ttm') or self._get_number('trailingpe') or self._get_number('pe')
        self.ps = self._get_number('p_s_ttm') or self._get_number('trailingsales') or self._get_number('ps')
        self.dividend_rate = self._get_number('dividend_rate') or self._get_number('dividend')
        self.dividend_yield = self._get_number('dividend_yield')

        # try to derive missing values from what we do have
        self._fill_derived()

    def _normalize_keys(self, d):
        norm = {}
        for k, v in d.items():
            if not isinstance(k, str):
                continue
            kn = k.strip().lower()
            # replace non-alphanumeric with underscore and collapse multiple underscores
            kn = re.sub(r'[^0-9a-z]+', '_', kn).strip('_')
            norm[kn] = v
        return norm

    def _parse_number(self, v):
        if v is None:
            return None
        # already numeric
        if isinstance(v, (int, float)):
            return float(v)
        # try strings: strip currency, commas, parentheses, percent
        try:
            s = str(v).strip()
            # handle parentheses for negatives: "(1,234)" -> -1234
            neg = False
            if s.startswith('(') and s.endswith(')'):
                neg = True
                s = s[1:-1].strip()
            # percent
            if s.endswith('%'):
                num = float(s[:-1].replace(',', '').replace('$', '')) / 100.0
                return -num if neg else num
            # handle K/M/B suffixes (common in UIs)
            m = re.match(r'^([+-]?[\d,.]+)\s*([kmbKMb]?)$', s)
            if m:
                base = float(m.group(1).replace(',', ''))
                suf = m.group(2).lower()
                if suf == 'k':
                    base *= 1e3
                elif suf == 'm':
                    base *= 1e6
                elif suf == 'b':
                    base *= 1e9
                return -base if neg else base
            # fallback generic clean
            num = float(s.replace(',', '').replace('$', ''))
            return -num if neg else num
        except Exception:
            return None

    def _get_number(self, key):
        # accept both the canonical key and several common aliases
        if not key:
            return None
        k = key.lower()
        v = self._norm.get(k)
        if v is None:
            # try a few common naming variants
            alt = k.replace('_', '')
            for nk, nv in self._norm.items():
                if nk.replace('_', '') == alt:
                    v = nv
                    break
        return self._parse_number(v)

    def _extract_price(self):
        # attempt to read common price keys from normalized dict
        for k in ('price', 'currentprice', 'lastprice', 'previousclose'):
            v = self._norm.get(k)
            if v is not None:
                p = self._parse_number(v)
                if p is not None:
                    return p
        # also check info-style keys
        for k in ('regularmarketprice', 'regularmarketpreviousclose', 'previousclose'):
            v = self._norm.get(k)
            if v is not None:
                p = self._parse_number(v)
                if p is not None:
                    return p
        return None

    def _infer_tax_rate(self):
        tp = self._get_number('tax_provision') or self._get_number('tax_ttm') or self._get_number('tax')
        pretax = self._get_number('pretax_income') or self._get_number('ebt_ttm')
        if tp and pretax and pretax != 0:
            return tp / pretax
        return None

    def _fill_derived(self):
        # Price: try to get from normalized raw if not passed
        if self.price is None:
            self.price = self._extract_price()

        # Market cap <-> shares relation
        if (self.market_cap is None) and (self.shares is not None) and (self.price is not None):
            try:
                self.market_cap = float(self.shares) * float(self.price)
            except Exception:
                pass

        if (self.shares is None) and (self.market_cap is not None) and (self.price is not None) and self.price != 0:
            try:
                self.shares = float(self.market_cap) / float(self.price)
            except Exception:
                pass

        # Enterprise value from market cap + debt - cash
        if (self.enterprise_value is None) and (self.market_cap is not None):
            debt = self.total_debt or 0.0
            cash = self.total_cash or 0.0
            try:
                self.enterprise_value = float(self.market_cap) + float(debt) - float(cash)
            except Exception:
                pass

        # Revenue estimate from market_cap / PS if missing
        if (self.revenue_ttm is None) and (self.ps is not None) and (self.market_cap is not None) and self.ps != 0:
            try:
                self.revenue_ttm = float(self.market_cap) / float(self.ps)
            except Exception:
                pass

        # Net income estimate from market_cap / P/E if missing
        if (self.net_income_ttm is None) and (self.pe is not None) and (self.market_cap is not None) and self.pe != 0:
            try:
                self.net_income_ttm = float(self.market_cap) / float(self.pe)
            except Exception:
                pass

        # Dividend yield from dividend rate and price
        if (self.dividend_yield is None) and (self.dividend_rate is not None) and (self.price is not None) and self.price != 0:
            try:
                self.dividend_yield = float(self.dividend_rate) / float(self.price)
            except Exception:
                pass

        # If tax_rate still missing try to infer again (may have become available)
        if self.tax_rate is None:
            self.tax_rate = self._infer_tax_rate()

    def get_relative_value(self):
        """
        Compute simple relative multiples: P/E, P/S, EV/Revenue, EV/EBIT, P/B.
        Returns a dict with None for missing entries.
        """
        res = {}
        res['P/E'] = self.pe if self.pe is not None else ( (self.market_cap / self.net_income_ttm) if (self.market_cap and self.net_income_ttm) else None)
        res['P/S'] = self.ps if self.ps is not None else (self.market_cap / self.revenue_ttm if self.market_cap and self.revenue_ttm else None)
        res['P/B'] = self.price_to_book
        res['EV/Revenue'] = (self.enterprise_value / self.revenue_ttm) if (self.enterprise_value and self.revenue_ttm) else None
        res['EV/EBIT'] = (self.enterprise_value / self.ebit_ttm) if (self.enterprise_value and self.ebit_ttm) else None
        # Debt/Equity: approximate equity = market_cap - total_debt (if total_debt smaller); else None
        try:
            equity = None
            if self.market_cap is not None:
                equity = float(self.market_cap)
            if equity is not None and self.total_debt is not None:
                denom = equity - float(self.total_debt)
                if denom and denom != 0:
                    res['Debt/Equity'] = float(self.total_debt) / denom
                else:
                    res['Debt/Equity'] = None
            else:
                res['Debt/Equity'] = None
        except Exception:
            res['Debt/Equity'] = None
        return res

    def _estimate_fcf_ttm(self):
        """
        Estimate TTM Free Cash Flow using available items:
        FCF = Net Income + Depreciation - CapEx - Change in WC
        Falls back to Operating Income*(1-tax) - CapEx if necessary.
        """
        if self.net_income_ttm is not None:
            dep = self.depr_ttm or 0.0
            capex = self.capex_ttm or 0.0
            change_wc = self.change_wc or 0.0
            try:
                return float(self.net_income_ttm) + float(dep) - float(capex) - float(change_wc)
            except Exception:
                return None

        if self.ebit_ttm is not None:
            tax = self.tax_rate if self.tax_rate is not None else 0.25
            capex = self.capex_ttm or 0.0
            try:
                return float(self.ebit_ttm) * (1 - float(tax)) - float(capex)
            except Exception:
                return None

        return None

    def get_dcf(self, years=5, growth=0.03, discount=0.10, terminal_growth=0.02, terminal_multiple=None):
        """
        Simple DCF:
        - projects FCF from a TTM estimate
        - uses constant growth for projection and either Gordon or exit multiple for terminal value
        Returns dict: {'dcf_pv': float, 'equity_value': float, 'intrinsic_price': float, 'fcf_ttm_estimate': float}
        """
        fcf0 = self._estimate_fcf_ttm()
        if fcf0 is None or discount <= 0:
            return {'dcf_pv': None, 'equity_value': None, 'intrinsic_price': None, 'fcf_ttm_estimate': None}

        fcf_projs = [fcf0 * ((1 + growth) ** y) for y in range(1, years + 1)]
        pv_projs = sum([fcf_projs[i] / ((1 + discount) ** (i + 1)) for i in range(len(fcf_projs))])

        # terminal value
        if terminal_multiple and fcf_projs[-1] is not None:
            tv = fcf_projs[-1] * terminal_multiple
        else:
            r = discount
            g = terminal_growth
            if r <= g:
                return {'dcf_pv': None, 'equity_value': None, 'intrinsic_price': None, 'fcf_ttm_estimate': fcf0}
            tv = fcf_projs[-1] * (1 + g) / (r - g)

        pv_tv = tv / ((1 + discount) ** years)
        enterprise_value_est = pv_projs + pv_tv

        debt = self.total_debt or 0.0
        cash = self.total_cash or 0.0
        equity_value = enterprise_value_est - debt + cash

        intrinsic_price = (equity_value / self.shares) if (equity_value is not None and self.shares and self.shares > 0) else None

        return {
            'dcf_pv': enterprise_value_est,
            'equity_value': equity_value,
            'intrinsic_price': intrinsic_price,
            'fcf_ttm_estimate': fcf0
        }

    def get_growth_dividend_valuation(self, required_return=0.10, growth=0.02):
        """
        Gordon Growth if dividend info available. If only yield is present, compute D0 = yield * price.
        Returns {'gordon_value': float, 'dividend_annual': float}
        """
        d = None
        if self.dividend_rate:
            d = self.dividend_rate
        elif self.dividend_yield and self.price:
            d = self.dividend_yield * self.price
        else:
            return {'gordon_value': None, 'dividend_annual': None}

        r = required_return
        g = growth
        if r <= g:
            return {'gordon_value': None, 'dividend_annual': d}

        intrinsic_price = d * (1 + g) / (r - g)
        return {'gordon_value': intrinsic_price, 'dividend_annual': d}

    def summary(self):
        out = {'ticker': self.ticker}
        out.update(self.get_relative_value())
        out.update(self.get_dcf())
        out.update(self.get_growth_dividend_valuation())
        return out