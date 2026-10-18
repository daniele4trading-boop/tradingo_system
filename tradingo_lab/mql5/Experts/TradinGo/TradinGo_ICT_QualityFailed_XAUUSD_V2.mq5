//+------------------------------------------------------------------+
//| TradinGo ICT Quality/Failed Continuation EA                      |
//| Research EA for XAUUSD M1. Demo/backtest first; live disabled     |
//| by default unless RequireDemoAccount is turned off explicitly.    |
//+------------------------------------------------------------------+
#property copyright "TradinGo Lab"
#property version   "1.000"
#property strict

#include <Trade/Trade.mqh>

enum ENUM_TG_STRATEGY_MODE
{
   TG_QUALITY_PLUS_FAILED = 0,
   TG_CORE_WITH_FAILED_REPLACEMENT = 1,
   TG_QUALITY_ONLY = 2,
   TG_FAILED_ONLY = 3,
   TG_CORE_ONLY = 4
};

enum ENUM_TG_SESSION_CLOCK
{
   TG_SESSION_EUROPE_ROME = 0,
   TG_SESSION_SERVER_TIME = 1
};

input string                InpTradeSymbol = "";
input ENUM_TIMEFRAMES       InpSignalTimeframe = PERIOD_M1;
input ENUM_TG_STRATEGY_MODE InpStrategyMode = TG_QUALITY_PLUS_FAILED;
input long                  InpMagicNumber = 2026060202;

input bool                  InpEnableTrading = true;
input bool                  InpRequireDemoAccount = true;
input bool                  InpAllowMultiplePositions = false;
input bool                  InpOneTradePerBar = true;
input bool                  InpStopTradingAfterDailyLoss = false;
input int                   InpMaxDailyTrades = 0;

input int                   InpMaxSpreadPoints = 25;
input bool                  InpCheckSignalBarSpread = true;
input bool                  InpCheckCurrentSpread = true;

input ENUM_TG_SESSION_CLOCK InpSessionClock = TG_SESSION_EUROPE_ROME;
input int                   InpServerUtcOffsetHours = 0;
input int                   InpSessionStartHour = 15;
input int                   InpSessionStartMinute = 0;
input int                   InpSessionEndHour = 20;
input int                   InpSessionEndMinute = 0;

input int                   InpSweepLookbackBars = 20;
input int                   InpAtrPeriod = 14;
input double                InpDisplacementAtrMultiplier = 1.20;

input int                   InpQualityTargetPoints = 450;
input int                   InpQualityStopPoints = 80;
input int                   InpQualityHorizonBars = 15;
input double                InpQualityAtrMaxPoints = 279.0;
input double                InpQualityDirPrev10MinPoints = -322.0;

input int                   InpFailedTargetPoints = 500;
input int                   InpFailedStopPoints = 150;
input int                   InpFailedHorizonBars = 15;
input double                InpFailedAtrMinPoints = 450.0;
input double                InpFailedDirPrev10MaxPoints = -100.0;
input double                InpFailedBodyAtrMin = 1.20;

input bool                  InpUseVolumeFilter = false;
input bool                  InpVolumeFilterFailedOnly = true;
input int                   InpVolumeLookbackBars = 20;
input double                InpMinRelativeVolume = 1.20;
input double                InpMaxRelativeVolume = 3.50;

input bool                  InpUseRiskPercent = true;
input double                InpRiskPercent = 1.0;
input double                InpFixedLots = 1.0;
input bool                  InpRiskIncludesSlippage = true;
input int                   InpEstimatedRoundTripSlippagePoints = 10;

input bool                  InpPrintSignalDiagnostics = true;
input bool                  InpPrintOnlyWhenIctSignal = true;
input bool                  InpPrintSessionDiagnostics = false;
input int                   InpDiagnosticsMaxLines = 500;
input int                   InpDeviationPoints = 30;

CTrade trade;
int atr_handle = INVALID_HANDLE;
datetime last_bar_time = 0;
datetime last_trade_signal_bar_time = 0;
int diagnostics_lines_printed = 0;

struct TradeDecision
{
   bool should_trade;
   int direction;
   int target_points;
   int stop_points;
   int horizon_bars;
   string component;
   string reason;
   double atr_points;
   double dir_prev10_points;
   double relative_volume;
   int signal_bar_spread;
};

//+------------------------------------------------------------------+
int OnInit()
{
   string symbol = EffectiveSymbol();
   if(!SymbolSelect(symbol, true))
   {
      Print("Unable to select symbol: ", symbol);
      return INIT_FAILED;
   }

   atr_handle = iATR(symbol, InpSignalTimeframe, InpAtrPeriod);
   if(atr_handle == INVALID_HANDLE)
   {
      Print("Unable to create ATR handle for ", symbol);
      return INIT_FAILED;
   }

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpDeviationPoints);

   Print("TradinGo ICT Quality/Failed EA V2 initialized on ", symbol,
         " mode=", EnumToString(InpStrategyMode),
         " max_spread=", InpMaxSpreadPoints,
         " demo_required=", InpRequireDemoAccount);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(atr_handle != INVALID_HANDLE)
      IndicatorRelease(atr_handle);
}

//+------------------------------------------------------------------+
void OnTick()
{
   string symbol = EffectiveSymbol();
   if(_Symbol != symbol && InpTradeSymbol != "")
   {
      Print("Symbol mismatch: chart/tester symbol=", _Symbol,
            " InpTradeSymbol=", InpTradeSymbol,
            ". Set InpTradeSymbol empty to use chart/tester symbol.");
      return;
   }

   ManageOpenPositions(symbol);

   datetime bar_time = CurrentBarTime(symbol);
   if(bar_time == 0)
      return;
   if(InpOneTradePerBar && bar_time == last_bar_time)
      return;
   last_bar_time = bar_time;

   if(!InpEnableTrading)
      return;
   if(!TradingAccountAllowed())
      return;
   if(!InpAllowMultiplePositions && CountOpenPositions(symbol) > 0)
      return;
   if(DailyRiskBlockActive(symbol))
      return;

   TradeDecision decision;
   if(!BuildDecision(symbol, decision))
      return;

   PrintDecisionDiagnostics(symbol, decision);

   if(!decision.should_trade)
      return;
   if(InpOneTradePerBar && SignalBarTime(symbol) == last_trade_signal_bar_time)
      return;

   if(OpenDecisionTrade(symbol, decision))
      last_trade_signal_bar_time = SignalBarTime(symbol);
}

//+------------------------------------------------------------------+
string EffectiveSymbol()
{
   if(InpTradeSymbol == "")
      return _Symbol;
   return InpTradeSymbol;
}

//+------------------------------------------------------------------+
bool TradingAccountAllowed()
{
   if(MQLInfoInteger(MQL_TESTER))
      return true;
   if(!InpRequireDemoAccount)
      return true;

   long mode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(mode == ACCOUNT_TRADE_MODE_DEMO)
      return true;

   Print("Trading blocked: RequireDemoAccount=true and account is not demo.");
   return false;
}

//+------------------------------------------------------------------+
datetime CurrentBarTime(const string symbol)
{
   datetime times[];
   ArraySetAsSeries(times, true);
   if(CopyTime(symbol, InpSignalTimeframe, 0, 1, times) != 1)
      return 0;
   return times[0];
}

//+------------------------------------------------------------------+
datetime SignalBarTime(const string symbol)
{
   datetime times[];
   ArraySetAsSeries(times, true);
   if(CopyTime(symbol, InpSignalTimeframe, 1, 1, times) != 1)
      return 0;
   return times[0];
}

//+------------------------------------------------------------------+
bool BuildDecision(const string symbol, TradeDecision &decision)
{
   decision.should_trade = false;
   decision.direction = 0;
   decision.target_points = 0;
   decision.stop_points = 0;
   decision.horizon_bars = 0;
   decision.component = "NONE";
   decision.reason = "no_signal";
   decision.atr_points = 0.0;
   decision.dir_prev10_points = 0.0;
   decision.relative_volume = 0.0;
   decision.signal_bar_spread = 0;

   int required_bars = MathMax(MathMax(InpSweepLookbackBars + 3, InpVolumeLookbackBars + 3), 12);
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(symbol, InpSignalTimeframe, 0, required_bars + 5, rates);
   if(copied < required_bars)
   {
      decision.reason = "not_enough_bars";
      return true;
   }

   MqlRates signal_bar = rates[1];
   decision.signal_bar_spread = (int)signal_bar.spread;

   if(!InSession(signal_bar.time))
   {
      decision.reason = "outside_session signal_time=" + TimeToString(signal_bar.time, TIME_DATE|TIME_MINUTES)
                        + " session_time=" + TimeToString(ConvertToSessionTime(signal_bar.time), TIME_DATE|TIME_MINUTES);
      return true;
   }

   if(InpCheckSignalBarSpread && decision.signal_bar_spread > InpMaxSpreadPoints)
   {
      decision.reason = "signal_bar_spread_too_high";
      return true;
   }

   int current_spread = CurrentSpreadPoints(symbol);
   if(InpCheckCurrentSpread && current_spread > InpMaxSpreadPoints)
   {
      decision.reason = "current_spread_too_high";
      return true;
   }

   double atr = IndicatorValue(atr_handle, 0, 1);
   if(atr <= 0.0)
   {
      decision.reason = "atr_unavailable";
      return true;
   }

   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   decision.atr_points = atr / point;

   double previous_high = rates[2].high;
   double previous_low = rates[2].low;
   for(int i = 2; i < 2 + InpSweepLookbackBars && i < copied; i++)
   {
      previous_high = MathMax(previous_high, rates[i].high);
      previous_low = MathMin(previous_low, rates[i].low);
   }

   bool sweep_high = (signal_bar.high > previous_high && signal_bar.close < previous_high);
   bool sweep_low = (signal_bar.low < previous_low && signal_bar.close > previous_low);

   double body = MathAbs(signal_bar.close - signal_bar.open);
   bool displacement_up = (signal_bar.close > signal_bar.open && body > atr * InpDisplacementAtrMultiplier);
   bool displacement_down = (signal_bar.open > signal_bar.close && body > atr * InpDisplacementAtrMultiplier);

   int reversal_signal = 0;
   if(sweep_low && displacement_up)
      reversal_signal = 1;
   else if(sweep_high && displacement_down)
      reversal_signal = -1;

   if(reversal_signal == 0)
   {
      decision.reason = "no_ict_reversal_displacement";
      return true;
   }

   decision.dir_prev10_points = ((signal_bar.close - rates[11].close) / point) * reversal_signal;
   double body_atr = body / atr;
   decision.relative_volume = RelativeVolume(rates, copied, 1, InpVolumeLookbackBars);

   bool quality = (decision.atr_points <= InpQualityAtrMaxPoints &&
                   decision.dir_prev10_points > InpQualityDirPrev10MinPoints);
   bool failed = (decision.atr_points >= InpFailedAtrMinPoints &&
                  decision.dir_prev10_points <= InpFailedDirPrev10MaxPoints &&
                  body_atr >= InpFailedBodyAtrMin);

   if(InpStrategyMode == TG_QUALITY_PLUS_FAILED)
   {
      if(quality)
      {
         if(!VolumeFilterAllows(decision, false))
            return true;
         FillDecision(decision, reversal_signal, InpQualityTargetPoints, InpQualityStopPoints, InpQualityHorizonBars, "Q_REVERSAL", "quality_reversal");
         return true;
      }
      if(failed)
      {
         if(!VolumeFilterAllows(decision, true))
            return true;
         FillDecision(decision, -reversal_signal, InpFailedTargetPoints, InpFailedStopPoints, InpFailedHorizonBars, "F_CONT", "failed_continuation");
         return true;
      }
      decision.reason = "signal_not_quality_or_failed";
      return true;
   }

   if(InpStrategyMode == TG_CORE_WITH_FAILED_REPLACEMENT)
   {
      if(failed)
      {
         if(!VolumeFilterAllows(decision, true))
            return true;
         FillDecision(decision, -reversal_signal, InpFailedTargetPoints, InpFailedStopPoints, InpFailedHorizonBars, "F_CONT", "failed_replaces_core");
         return true;
      }
      if(!VolumeFilterAllows(decision, false))
         return true;
      FillDecision(decision, reversal_signal, InpQualityTargetPoints, InpQualityStopPoints, InpQualityHorizonBars, "CORE_REV", "core_reversal");
      return true;
   }

   if(InpStrategyMode == TG_QUALITY_ONLY)
   {
      if(quality)
      {
         if(!VolumeFilterAllows(decision, false))
            return true;
         FillDecision(decision, reversal_signal, InpQualityTargetPoints, InpQualityStopPoints, InpQualityHorizonBars, "Q_REVERSAL", "quality_reversal");
      }
      else
         decision.reason = "not_quality";
      return true;
   }

   if(InpStrategyMode == TG_FAILED_ONLY)
   {
      if(failed)
      {
         if(!VolumeFilterAllows(decision, true))
            return true;
         FillDecision(decision, -reversal_signal, InpFailedTargetPoints, InpFailedStopPoints, InpFailedHorizonBars, "F_CONT", "failed_continuation");
      }
      else
         decision.reason = "not_failed";
      return true;
   }

   if(InpStrategyMode == TG_CORE_ONLY)
   {
      if(!VolumeFilterAllows(decision, false))
         return true;
      FillDecision(decision, reversal_signal, InpQualityTargetPoints, InpQualityStopPoints, InpQualityHorizonBars, "CORE_REV", "core_reversal");
      return true;
   }

   decision.reason = "unsupported_mode";
   return true;
}

//+------------------------------------------------------------------+
void PrintDecisionDiagnostics(const string symbol, const TradeDecision &decision)
{
   if(!InpPrintSignalDiagnostics)
      return;
   if(InpDiagnosticsMaxLines > 0 && diagnostics_lines_printed >= InpDiagnosticsMaxLines)
      return;

   bool signal_related = (decision.reason != "outside_session" &&
                          StringFind(decision.reason, "outside_session") != 0 &&
                          decision.reason != "no_ict_reversal_displacement");
   bool outside_session = (StringFind(decision.reason, "outside_session") == 0);
   if(outside_session && !InpPrintSessionDiagnostics)
      return;
   if(InpPrintOnlyWhenIctSignal && !signal_related && !decision.should_trade && !outside_session)
      return;

   diagnostics_lines_printed++;
   Print("TG_DIAG trade=", decision.should_trade,
         " component=", decision.component,
         " reason=", decision.reason,
         " dir=", decision.direction,
         " atr_points=", DoubleToString(decision.atr_points, 1),
         " dir_prev10=", DoubleToString(decision.dir_prev10_points, 1),
         " rel_volume=", DoubleToString(decision.relative_volume, 2),
         " signal_spread=", decision.signal_bar_spread,
         " current_spread=", CurrentSpreadPoints(symbol),
         " chart_symbol=", _Symbol,
         " trade_symbol=", symbol);
}

//+------------------------------------------------------------------+
void FillDecision(TradeDecision &decision, const int direction, const int target_points, const int stop_points, const int horizon_bars, const string component, const string reason)
{
   decision.should_trade = true;
   decision.direction = direction;
   decision.target_points = target_points;
   decision.stop_points = stop_points;
   decision.horizon_bars = horizon_bars;
   decision.component = component;
   decision.reason = reason;
}

//+------------------------------------------------------------------+
double RelativeVolume(const MqlRates &rates[], const int copied, const int signal_shift, const int lookback_bars)
{
   if(lookback_bars <= 0)
      return 0.0;

   int start = signal_shift + 1;
   int end = start + lookback_bars;
   if(copied < end)
      return 0.0;

   double total_volume = 0.0;
   int count = 0;
   for(int i = start; i < end; i++)
   {
      if(rates[i].tick_volume <= 0)
         continue;
      total_volume += (double)rates[i].tick_volume;
      count++;
   }
   if(count <= 0 || total_volume <= 0.0)
      return 0.0;

   return (double)rates[signal_shift].tick_volume / (total_volume / count);
}

//+------------------------------------------------------------------+
bool VolumeFilterAllows(TradeDecision &decision, const bool failed_candidate)
{
   if(!InpUseVolumeFilter)
      return true;
   if(InpVolumeFilterFailedOnly && !failed_candidate)
      return true;

   if(decision.relative_volume <= 0.0)
   {
      decision.reason = "volume_unavailable";
      return false;
   }
   if(InpMinRelativeVolume > 0.0 && decision.relative_volume < InpMinRelativeVolume)
   {
      decision.reason = "relative_volume_too_low";
      return false;
   }
   if(InpMaxRelativeVolume > 0.0 && decision.relative_volume > InpMaxRelativeVolume)
   {
      decision.reason = "relative_volume_too_high";
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
double IndicatorValue(const int handle, const int buffer, const int shift)
{
   double values[];
   if(CopyBuffer(handle, buffer, shift, 1, values) != 1)
      return EMPTY_VALUE;
   return values[0];
}

//+------------------------------------------------------------------+
int CurrentSpreadPoints(const string symbol)
{
   long spread = 0;
   if(SymbolInfoInteger(symbol, SYMBOL_SPREAD, spread))
      return (int)spread;

   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(point <= 0.0)
      return 0;
   return (int)MathRound((ask - bid) / point);
}

//+------------------------------------------------------------------+
bool InSession(const datetime server_time)
{
   datetime session_time = ConvertToSessionTime(server_time);

   MqlDateTime dt;
   TimeToStruct(session_time, dt);
   int minutes = dt.hour * 60 + dt.min;
   int start_minutes = InpSessionStartHour * 60 + InpSessionStartMinute;
   int end_minutes = InpSessionEndHour * 60 + InpSessionEndMinute;

   if(start_minutes <= end_minutes)
      return (minutes >= start_minutes && minutes < end_minutes);
   return (minutes >= start_minutes || minutes < end_minutes);
}

//+------------------------------------------------------------------+
datetime ConvertToSessionTime(const datetime server_time)
{
   if(InpSessionClock == TG_SESSION_SERVER_TIME)
      return server_time;

   datetime utc_time = server_time - InpServerUtcOffsetHours * 3600;
   return utc_time + EuropeRomeOffsetHours(utc_time) * 3600;
}

//+------------------------------------------------------------------+
int EuropeRomeOffsetHours(const datetime utc_time)
{
   MqlDateTime dt;
   TimeToStruct(utc_time, dt);
   datetime dst_start = LastSundayAtOneUtc(dt.year, 3);
   datetime dst_end = LastSundayAtOneUtc(dt.year, 10);
   if(utc_time >= dst_start && utc_time < dst_end)
      return 2;
   return 1;
}

//+------------------------------------------------------------------+
datetime LastSundayAtOneUtc(const int year, const int month)
{
   MqlDateTime dt;
   ZeroMemory(dt);
   dt.year = year;
   dt.mon = month;
   dt.day = 31;
   dt.hour = 1;
   dt.min = 0;
   dt.sec = 0;
   datetime t = StructToTime(dt);
   MqlDateTime current;
   TimeToStruct(t, current);
   while(current.day_of_week != 0)
   {
      t -= 86400;
      TimeToStruct(t, current);
   }
   return t;
}

//+------------------------------------------------------------------+
bool OpenDecisionTrade(const string symbol, const TradeDecision &decision)
{
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double volume = CalculateVolume(symbol, decision.stop_points);
   if(volume <= 0.0)
   {
      Print("Invalid volume calculated.");
      return false;
   }

   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   string comment = "TG_" + decision.component + "_H" + IntegerToString(decision.horizon_bars);

   bool ok = false;
   if(decision.direction == 1)
   {
      double sl = NormalizeDouble(ask - decision.stop_points * point, digits);
      double tp = NormalizeDouble(ask + decision.target_points * point, digits);
      ok = trade.Buy(volume, symbol, 0.0, sl, tp, comment);
   }
   else if(decision.direction == -1)
   {
      double sl = NormalizeDouble(bid + decision.stop_points * point, digits);
      double tp = NormalizeDouble(bid - decision.target_points * point, digits);
      ok = trade.Sell(volume, symbol, 0.0, sl, tp, comment);
   }

   if(!ok)
   {
      Print("Order failed. retcode=", trade.ResultRetcode(),
            " desc=", trade.ResultRetcodeDescription(),
            " component=", decision.component);
      return false;
   }

   Print("Order opened: ", decision.component,
         " dir=", decision.direction,
         " lots=", DoubleToString(volume, 2),
         " target=", decision.target_points,
         " stop=", decision.stop_points,
         " horizon=", decision.horizon_bars,
         " retcode=", trade.ResultRetcode());
   return true;
}

//+------------------------------------------------------------------+
double CalculateVolume(const string symbol, const int stop_points)
{
   double min_volume = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double max_volume = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      step = 0.01;

   if(!InpUseRiskPercent)
      return NormalizeVolume(InpFixedLots, min_volume, max_volume, step);

   double tick_value = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(tick_value <= 0.0 || tick_size <= 0.0 || point <= 0.0)
      return NormalizeVolume(InpFixedLots, min_volume, max_volume, step);

   double point_value_per_lot = tick_value / (tick_size / point);
   double risk_amount = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;
   double risk_points = stop_points;
   if(InpRiskIncludesSlippage)
      risk_points += InpEstimatedRoundTripSlippagePoints;

   double volume = risk_amount / (risk_points * point_value_per_lot);
   return NormalizeVolume(volume, min_volume, max_volume, step);
}

//+------------------------------------------------------------------+
double NormalizeVolume(const double volume, const double min_volume, const double max_volume, const double step)
{
   double clipped = MathMax(min_volume, MathMin(max_volume, volume));
   double normalized = MathFloor(clipped / step) * step;
   if(normalized < min_volume)
      normalized = min_volume;
   return normalized;
}

//+------------------------------------------------------------------+
int CountOpenPositions(const string symbol)
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
         continue;
      count++;
   }
   return count;
}

//+------------------------------------------------------------------+
bool DailyRiskBlockActive(const string symbol)
{
   if(InpMaxDailyTrades <= 0 && !InpStopTradingAfterDailyLoss)
      return false;

   datetime day_start = ServerDayStart(TimeCurrent());
   datetime now = TimeCurrent();
   if(!HistorySelect(day_start, now))
      return false;

   int entries_today = 0;
   bool has_loss_today = false;
   int total_deals = HistoryDealsTotal();
   for(int i = 0; i < total_deals; i++)
   {
      ulong deal_ticket = HistoryDealGetTicket(i);
      if(deal_ticket == 0)
         continue;
      if(HistoryDealGetString(deal_ticket, DEAL_SYMBOL) != symbol)
         continue;
      if(HistoryDealGetInteger(deal_ticket, DEAL_MAGIC) != InpMagicNumber)
         continue;

      ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
      if(entry == DEAL_ENTRY_IN)
         entries_today++;
      if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY || entry == DEAL_ENTRY_INOUT)
      {
         double pnl = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT)
                    + HistoryDealGetDouble(deal_ticket, DEAL_SWAP)
                    + HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
         if(pnl < 0.0)
            has_loss_today = true;
      }
   }

   if(InpStopTradingAfterDailyLoss && has_loss_today)
   {
      if(InpPrintSignalDiagnostics)
         Print("Daily risk block: losing closed trade already occurred today.");
      return true;
   }

   if(InpMaxDailyTrades > 0 && entries_today >= InpMaxDailyTrades)
   {
      if(InpPrintSignalDiagnostics)
         Print("Daily risk block: max daily trades reached. entries_today=", entries_today,
               " max=", InpMaxDailyTrades);
      return true;
   }

   return false;
}

//+------------------------------------------------------------------+
datetime ServerDayStart(const datetime value)
{
   MqlDateTime dt;
   TimeToStruct(value, dt);
   dt.hour = 0;
   dt.min = 0;
   dt.sec = 0;
   return StructToTime(dt);
}

//+------------------------------------------------------------------+
void ManageOpenPositions(const string symbol)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
         continue;

      datetime open_time = (datetime)PositionGetInteger(POSITION_TIME);
      string comment = PositionGetString(POSITION_COMMENT);
      int horizon = HorizonFromComment(comment);
      int shift = iBarShift(symbol, InpSignalTimeframe, open_time, false);
      if(shift >= horizon && horizon > 0)
      {
         if(trade.PositionClose(ticket))
            Print("Position closed by horizon. ticket=", ticket, " horizon=", horizon, " shift=", shift);
         else
            Print("Failed to close by horizon. ticket=", ticket, " retcode=", trade.ResultRetcodeDescription());
      }
   }
}

//+------------------------------------------------------------------+
int HorizonFromComment(const string comment)
{
   int pos = StringFind(comment, "_H");
   if(pos < 0)
      return MathMax(InpQualityHorizonBars, InpFailedHorizonBars);
   string value = StringSubstr(comment, pos + 2);
   int horizon = (int)StringToInteger(value);
   if(horizon <= 0)
      return MathMax(InpQualityHorizonBars, InpFailedHorizonBars);
   return horizon;
}

//+------------------------------------------------------------------+

