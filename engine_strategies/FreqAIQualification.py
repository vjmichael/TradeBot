"""Synthetic backtest smoke test only. Not a deployable trading strategy."""
from freqtrade.strategy import IStrategy


class FreqAIQualification(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False
    minimal_roi = {"0": 0.015}
    stoploss = -0.05
    startup_candle_count = 24
    process_only_new_candles = True

    def feature_engineering_expand_all(self, dataframe, period, **kwargs):
        dataframe["%-return-period"] = dataframe["close"].pct_change(period)
        dataframe["%-range-period"] = (dataframe["high"] - dataframe["low"]).rolling(period).mean() / dataframe["close"]
        return dataframe

    def feature_engineering_expand_basic(self, dataframe, **kwargs):
        dataframe["%-volume"] = dataframe["volume"]
        return dataframe

    def set_freqai_targets(self, dataframe, **kwargs):
        dataframe["&-return"] = dataframe["close"].shift(-3) / dataframe["close"] - 1
        return dataframe

    def populate_indicators(self, dataframe, metadata):
        return self.freqai.start(dataframe, metadata, self)

    def populate_entry_trend(self, dataframe, metadata):
        dataframe["enter_long"] = 0
        dataframe.loc[(dataframe["do_predict"] == 1) & (dataframe["volume"] > 0), "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        dataframe.loc[(dataframe["do_predict"] == 1) & (dataframe["date"].dt.hour % 8 == 0), "exit_long"] = 1
        return dataframe
