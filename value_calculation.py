import re
import numpy as np
import pandas as pd

class Stock:
    """
    Lightweight valuation helper that accepts a ticker, a metrics dict (variable shape),
    and optional price/history. Methods return dicts with computed values or None if data missing.
    """
    def __init__(self, ticker, metrics=None, history=None, price=None):
        self.ticker = ticker
        self.raw = metrics or {}
        self.history = history
        self.price = price if price is not None else self._extract_price()
        self._norm = self._normalize_keys(self.raw)
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
        # dividends
        self.dividend_rate = self._get_number('dividend_rate') or self._get_number('dividend') 
        self.dividend_yield = self._get_number('dividend_yield')

    def _normalize_keys(self, d):
        norm = {}
        for k, v in d.items():
            if not isinstance(k, str):
                continue
            kn = re.sub(r'[^0-9a-z_]', '_', k.lower()).strip('_')
            norm[kn] = v
        return norm

    def _get_number(self, key):
        v = self._norm.get(key)
        try:
            if v is None:
                return None
            return float(v)
        except Exception:
            return None

    def _extract_price(self):
        # attempt to read common price keys
        for k in ('price', 'currentprice', 'lastprice', 'previousclose'):
            v = self._norm.get(k)
            if v is not None:
                try:
                    return float(v)
                except Exception:
                    continue
        return None

    def _infer_tax_rate(self):
        # fallback: tax_rate = tax_provision / pretax_income if available
        tp = self._get_number('tax_provision') or self._get_number('tax_ttm') or self._get_number('tax')
        pretax = self._get_number('pretax_income') or self._get_number('ebt_ttm')
        if tp and pretax and pretax != 0:
            return tp / pretax
        return None

    def get_relative_value(self):
        """
        Compute simple relative multiples: P/E, P/S, EV/Revenue, EV/EBIT, P/B.
        Returns a dict with None for missing entries.
        """
        res = {}
        # P/E: prefer provided, else compute from market_cap/shares and net income
        res['P/E'] = self.pe if self.pe is not None else None
        res['P/S'] = self.ps if self.ps is not None else (self.market_cap / self.revenue_ttm if self.market_cap and self.revenue_ttm else None)
        res['P/B'] = self.price_to_book
        res['EV/Revenue'] = (self.enterprise_value / self.revenue_ttm) if (self.enterprise_value and self.revenue_ttm) else None
        res['EV/EBIT'] = (self.enterprise_value / self.ebit_ttm) if (self.enterprise_value and self.ebit_ttm) else None
        res['Debt/Equity'] = (self.total_debt / (self.market_cap - self.total_debt) if (self.total_debt and self.market_cap and self.market_cap > self.total_debt) else None)
        return res

    def _estimate_fcf_ttm(self):
        """
        Estimate TTM Free Cash Flow using available items:
        FCF = Net Income + Depreciation - CapEx - Change in WC
        Falls back to Operating Income*(1-tax) or EBITDA - CapEx if necessary.
        """
        if self.net_income_ttm is not None:
            dep = self.depr_ttm or 0.0
            capex = self.capex_ttm or 0.0
            change_wc = self.change_wc or 0.0
            return self.net_income_ttm + dep - capex - change_wc
        # fallback to after-tax operating income
        if self.ebit_ttm is not None:
            tax = self.tax_rate or 0.25
            return self.ebit_ttm * (1 - tax) - (self.capex_ttm or 0.0)
        return None

    def get_dcf(self, years=5, growth=0.03, discount=0.10, terminal_growth=0.02, terminal_multiple=None):
        """
        Simple DCF:
        - projects FCF from a TTM estimate
        - uses constant growth for projection and either Gordon or exit multiple for terminal value
        Returns dict: {'dcf_pv': float, 'intrinsic_value': float, 'intrinsic_price': float}
        """
        fcf0 = self._estimate_fcf_ttm()
        if fcf0 is None or discount <= 0:
            return {'dcf_pv': None, 'intrinsic_value': None, 'intrinsic_price': None}

        # project FCF
        fcf_projs = []
        for y in range(1, years + 1):
            fcf_projs.append(fcf0 * ((1 + growth) ** y))

        # PV of projections
        pv_projs = sum([fcf_projs[i] / ((1 + discount) ** (i + 1)) for i in range(len(fcf_projs))])

        # terminal value
        if terminal_multiple and self.enterprise_value is not None:
            # simple exit multiple on final year FCF
            tv = fcf_projs[-1] * terminal_multiple
        else:
            # Gordon growth on last projected FCF
            g = terminal_growth
            r = discount
            if r <= g:
                tv = None
            else:
                tv = fcf_projs[-1] * (1 + g) / (r - g)

        if tv is None:
            return {'dcf_pv': None, 'intrinsic_value': None, 'intrinsic_price': None}

        pv_tv = tv / ((1 + discount) ** years)
        enterprise_value_est = pv_projs + pv_tv

        # convert EV to equity value if we can
        if self.total_debt is not None or self.total_cash is not None:
            debt = self.total_debt or 0.0
            cash = self.total_cash or 0.0
            equity_value = enterprise_value_est - debt + cash
        else:
            equity_value = None

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
        Returns {'gordon_value': float, 'implied_price': float}
        """
        # get annual dividend
        d = None
        if self.dividend_rate:
            d = self.dividend_rate
        elif self.dividend_yield and self.price:
            d = self.dividend_yield * self.price
        else:
            return {'gordon_value': None, 'implied_price': None}

        r = required_return
        g = growth
        if r <= g:
            return {'gordon_value': None, 'implied_price': None}

        intrinsic_price = d * (1 + g) / (r - g)
        return {'gordon_value': intrinsic_price, 'dividend_annual': d}

    def summary(self):
        out = {'ticker': self.ticker}
        out.update(self.get_relative_value())
        out.update(self.get_dcf())
        out.update(self.get_growth_dividend_valuation())
        return out