"""Native strategy with no autonomous entries or position adjustment."""
from decimal import Decimal
from freqtrade.strategy import IStrategy


class GatePaper(IStrategy):
    INTERFACE_VERSION=3
    timeframe='1h'
    can_short=False
    position_adjustment_enable=False
    minimal_roi={}
    stoploss=-0.05
    startup_candle_count=24
    _permit=None

    def populate_indicators(self,dataframe,metadata):
        return dataframe

    def populate_entry_trend(self,dataframe,metadata):
        dataframe['enter_long']=0
        return dataframe

    def populate_exit_trend(self,dataframe,metadata):
        dataframe['exit_long']=0
        return dataframe

    def arm(self,tag,pair,entry):
        self._permit=(tag,pair,entry)

    def disarm(self):
        self._permit=None

    def confirm_trade_entry(self,pair,order_type,amount,rate,time_in_force,current_time,entry_tag,side,**kwargs):
        # The broker additionally guards exchange.create_order because upstream
        # strategy exception handling can default to allow.
        try:
            return bool(self._permit and self._permit==(entry_tag,pair,int(Decimal(str(rate))*100000000))
                        and order_type=='limit' and side=='long' and amount>0)
        except Exception:
            return False
