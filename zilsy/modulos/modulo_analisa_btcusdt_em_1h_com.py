import json
import os
from datetime import datetime, timezone
import pandas as pd
import requests
from b2sdk.v2 import B2Api, InMemoryAccountInfo


class BTCAnalyzer:
    """Analisador de mercado para BTCUSDT integrando contexto macroeconômico (Brasil)
    e gerenciamento de risco com salvamento dos relatórios no Backblaze B2.
    """

    def __init__(self, portfolio_balance_usd: float = 10000.0, risk_per_trade_pct: float = 1.0):
        self.portfolio_balance = portfolio_balance_usd
        self.risk_per_trade_pct = risk_per_trade_pct / 100.0

    def fetch_binance_klines(self, symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 100) -> pd.DataFrame:
        """Obtém dados históricos de velas (klines) da Binance."""
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        df = pd.DataFrame(
            data,
            columns=[
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "qav",
                "num_trades",
                "taker_base_vol",
                "taker_quote_vol",
                "ignore",
            ],
        )

        numeric_cols = ["open", "high", "low", "close", "volume"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, axis=1)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        return df

    def fetch_macro_data(self) -> dict:
        """Obtém a taxa SELIC atual (Banco Central do Brasil) e a cotação USD/BRL."""
        macro_info = {}

        try:
            url_selic = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/1?formato=json"
            resp_selic = requests.get(url_selic, timeout=10)
            resp_selic.raise_for_status()
            macro_info["selic_rate"] = float(resp_selic.json()[0]["valor"])
        except Exception as err:
            macro_info["selic_rate"] = None
            macro_info["selic_error"] = str(err)

        try:
            url_dolar = "https://economia.awesomeapi.com.br/json/last/USD-BRL"
            resp_dolar = requests.get(url_dolar, timeout=10)
            resp_dolar.raise_for_status()
            macro_info["usd_brl"] = float(resp_dolar.json()["USDBRL"]["bid"])
        except Exception as err:
            macro_info["usd_brl"] = None
            macro_info["dolar_error"] = str(err)

        return macro_info

    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """Calcula o RSI (Relative Strength Index)."""
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calcula o ATR (Average True Range) para definição de volatilidade e stops."""
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()

        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    def generate_analysis(self) -> dict:
        """Executa a análise técnica, avalia risco/stop e integra contexto macro."""
        df = self.fetch_binance_klines(symbol="BTCUSDT", interval="1h", limit=100)
        macro_data = self.fetch_macro_data()

        df["rsi"] = self.calculate_rsi(df["close"], period=14)
        df["atr"] = self.calculate_atr(df, period=14)

        current_candle = df.iloc[-1]
        current_price = float(current_candle["close"])
        current_rsi = float(current_candle["rsi"])
        current_atr = float(current_candle["atr"])

        stop_loss_distance = current_atr * 2.0
        stop_loss_price = current_price - stop_loss_distance
        take_profit_price = current_price + (stop_loss_distance * 2.0)

        max_capital_risk = self.portfolio_balance * self.risk_per_trade_pct
        position_size_btc = max_capital_risk / stop_loss_distance if stop_loss_distance > 0 else 0.0
        position_size_usd = position_size_btc * current_price

        if current_rsi >= 70:
            rsi_context = "SOBRECOMPRADO (Risco de Correção)"
        elif current_rsi <= 30:
            rsi_context = "SOBREVENDIDO (Oportunidade de Repique)"
        else:
            rsi_context = "NEUTRO"

        selic = macro_data.get("selic_rate") or 0.0
        dolar = macro_data.get("usd_brl") or 0.0
        btc_brl = current_price * dolar if dolar else 0.0

        macro_summary = (
            f"SELIC em {selic}% a.a. | Dólar: R$ {dolar:.2f}. "
            f"Preço estipulado do BTC em BRL: R$ {btc_brl:,.2f}."
        )

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "asset": "BTCUSDT",
            "timeframe": "1h",
            "price_metrics": {
                "current_price_usd": current_price,
                "rsi_14": round(current_rsi, 2),
                "atr_14": round(current_atr, 2),
                "rsi_status": rsi_context,
            },
            "macro_context": {
                "selic_rate_pct": selic,
                "usd_brl_rate": dolar,
                "btc_brl_price": round(btc_brl, 2),
                "summary": macro_summary,
            },
            "risk_management": {
                "portfolio_balance_usd": self.portfolio_balance,
                "account_risk_pct": self.risk_per_trade_pct * 100,
                "max_risk_usd": round(max_capital_risk, 2),
                "stop_loss_usd": round(stop_loss_price, 2),
                "take_profit_usd": round(take_profit_price, 2),
                "suggested_position_btc": round(position_size_btc, 6),
                "suggested_position_usd": round(position_size_usd, 2),
                "risk_reward_ratio": "1:2",
            },
        }

    @staticmethod
    def upload_to_b2(data: dict, bucket_name: str) -> None:
        """Salva a análise como arquivo JSON no Backblaze B2."""
        application_key_id = os.environ.get("B2_APPLICATION_KEY_ID")
        application_key = os.environ.get("B2_APPLICATION_KEY")

        if not application_key_id or not application_key:
            raise ValueError("Credenciais B2_APPLICATION_KEY_ID e B2_APPLICATION_KEY são obrigatórias.")

        info = InMemoryAccountInfo()
        b2_api = B2Api(info)
        b2_api.authorize_account("production", application_key_id, application_key)

        bucket = b2_api.get_bucket_by_name(bucket_name)

        file_data = json.dumps(data, indent=4).encode("utf-8")
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        file_name = f"analysis/btc_1h_risk_report_{timestamp_str}.json"

        bucket.upload_bytes(data_bytes=file_data, file_name=file_name)


if __name__ == "__main__":
    analyzer = BTCAnalyzer(portfolio_balance_usd=10000.0, risk_per_trade_pct=1.0)
    analysis_result = analyzer.generate_analysis()
    target_bucket = os.environ.get("B2_BUCKET_NAME", "my-crypto-analysis-bucket")

    if "B2_APPLICATION_KEY_ID" in os.environ and "B2_APPLICATION_KEY" in os.environ:
        analyzer.upload_to_b2(data=analysis_result, bucket_name=target_bucket)
    else:
        print(json.dumps(analysis_result, indent=4, ensure_ascii=False))