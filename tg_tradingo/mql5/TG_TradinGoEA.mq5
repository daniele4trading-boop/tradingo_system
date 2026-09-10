//+------------------------------------------------------------------+
//| TG_TradinGoEA.mq5                                                |
//| Legge segnali JSON dal bridge Python ed esegue ordini su MT5.    |
//| Distribuibile: ogni utente configura SignalsPath e LotMultiplier |
//+------------------------------------------------------------------+
#property copyright "TradinGo"
#property link      "https://github.com/daniele4trading-boop/tradingo_system"
#property version   "2.25"
#property description "JSON signal executor for TG TradinGo bridge"

//--- unica fonte di verita' della versione: allineata a BRIDGE_VERSION
#define EA_VERSION "2.25"

#include <Trade/Trade.mqh>
#include <Trade/PositionInfo.mqh>
#include <Trade/SymbolInfo.mqh>

//--- inputs
// Default: read signal_ch_*.json from MQL5\\Files (where the bridge writes).
// InpUseAbsolutePath: MT5 FileOpen is sandboxed to MQL5\\Files (and Common\\Files).
// Drive-letter paths like C:\\TG_TradinGo_Signals will fail with err=5004.
// Prefer a junction: MQL5\\Files\\tradingo -> share folder, then InpSignalsPath=tradingo\\
input string InpSignalsPath        = "";
input bool   InpUseAbsolutePath    = false;
input string InpChannels           = "gold,forex,oro,stark,ivan";
input string InpSymbolSuffix       = "";
input double InpLotMultiplier      = 1.0;
input double InpAddLotFactor       = 0.5;
// Per-channel absolute lot override (0 = use JSON fixed_lot * InpLotMultiplier).
// Moneta example: Ivan 0.02 / Stark 0.01 with InpChannels=ivan,stark
input double InpLotIvan            = 0.0;
input double InpLotStark           = 0.0;
input double InpLotGold            = 0.0;
input double InpLotOro             = 0.0;
input double InpLotForex           = 0.0;
// Short comment tags (empty = default CHANNEL name). Moneta: IT / AS
input string InpTagIvan            = "IT";
input string InpTagStark           = "AS";
input string InpTagGold            = "";
input string InpTagOro             = "";
input string InpTagForex           = "";
input bool   InpCommentUseTgPrefix = false; // false -> IT-T1 ; true -> TG-IT-T1
input int    InpMaxSlippagePoints  = 50;
input int    InpPollMs             = 500;
input int    InpRangeTolerancePoints = 200;
input int    InpOroRangeTolerancePoints = 250; // 0 = use InpRangeTolerancePoints
input bool   InpLogCancelledSignals  = true;
input bool   InpClearSignalAfterProcess = true;
input bool   InpIgnoreExistingOnInit = true; // skip+clear JSON already present at attach
input bool   InpStackOpensIfFlatBusy = true; // honor JSON allow_stack when positions already open
input int    InpStopBufferPoints   = 20;     // extra pts beyond stops/freeze level (avoid 10016)
// Off-market guard: skip an open whose entry/SL/TP are farther than this % from
// the current price (channel posting stale or wrong-scale levels). 0 = off.
input double InpMaxLevelDeviationPct = 2.0;
// Re-entry drift guard: an inherited re-entry keeps the SL of the original
// setup, so if the market already moved toward that SL the risk/reward is no
// longer the published one. Skip the re-entry when the drift from the signal
// entry exceeds this % of the entry->SL distance. 0 = off.
input double InpReentryMaxDriftPctOfSl = 40.0;
// A new setup arriving while positions are open (allow_stack=false) modifies
// their SL/TP: never apply a TP that is on the losing side of the position's
// open price, nor an SL already crossed by the market (would liquidate at once).
input bool   InpProtectExistingLevels = true;
// Naked open ("Gold sell now"): the levels arrive in a later message, so the
// tickets would stay unprotected until then (24 minutes on 2026-08-14, the
// channel had a typo in the zone). Open with a provisional SL this many points
// away, replaced by the real one on UPDATE_OPEN. 0 = open without SL.
input int    InpNakedFallbackSlPoints = 1200;
// Break-even commands must never make a position worse: when SL=entry is not
// legal (position in loss, or entry closer than the broker stops level) keep
// the current SL instead of clamping past the entry.
input bool   InpBeNeverWorseThanEntry = true;
// When SL=entry is not legal yet (price still within the stops level of the
// entry) the break-even stays pending and is retried on every tick until the
// market allows it; a position that closes meanwhile is simply dropped.
input bool   InpBePendingRetry        = true;
// UPDATE_SL moving the stop further away is a legitimate channel decision (give
// the price room), but the lot size was computed on the previous risk: cap the
// new stop at this multiple of the open->current SL distance instead of
// following it without limit. 0 = no cap (follow the channel exactly).
input double InpMaxSlWidenFactor = 2.0;
// Seconds within which positions opened together count as one batch
// (CLOSE_SELECTIVE keep=ALL_BUT_NEWEST closes only the newest batch).
input int    InpBatchWindowSec      = 120;
input bool   InpAutoBreakEvenOnTp1 = true;
input ulong  InpMagicOffset        = 0;

//--- v2.10 hardening / journal inputs
// Floating-loss killswitch. Compared in ACCOUNT_CURRENCY (see OnInit print). 0 = off.
input double InpMaxFloatingLossUSD   = 150.0;
input int    InpKillSwitchCooldownMin = 0;    // 0 = nessun blocco dopo killswitch bucket
input int    InpMaxHoldingMinutes    = 0;     // 0 = off (90 suggested): stale-position exit
input int    InpHeartbeatMaxAgeSec   = 180;   // 0 = off: block opens if bridge heartbeat stale
input string InpHeartbeatFile        = "tradingo_heartbeat.json";
input int    InpEquitySampleSec      = 60;    // 0 = off: equity curve sampling period
input string InpJournalPrefix        = "journal\\"; // relative under MQL5\\Files unless absolute
input bool   InpSingleInstanceLock   = true;  // one running EA instance per terminal
input int    InpLockStaleSec         = 120;   // lock expiry after a crash (no clean OnDeinit)

//--- v2.12 prop-account guards (iFunds): all defaults NEUTRAL (off) so the
//--- same build keeps running unchanged on the Vantage/Ultima demo accounts.
// Static equity drawdown floor. 0 = off (guard disabled entirely).
input double InpDdMaxPct             = 0.0;   // e.g. 6.0 -> floor = start*(1-6%)
input double InpDdStartEquity        = 0.0;   // 0 = capture equity at first init (persisted)
input double InpDdCloseAtPct         = 80.0;  // % of allowance consumed -> close all + halt
input double InpDdBlockNewAtPct      = 60.0;  // % of allowance consumed -> block new opens
// Recovery mode: below the block level, instead of refusing every signal, keep
// trading at this lot so the account can climb back above it. 0 = off (block).
input double InpDdRecoveryLot        = 0.0;   // e.g. 0.01 -> minimum-size recovery trades
// Manual kill switch: presence of this file blocks new opens (no restart needed).
// Existing positions stay under normal management. "" = disabled.
input string InpHaltFlagFile         = "tradingo_halt.flag";
// Reservoir-based sizing: lot = (reservoir * util%) / floating_per_001, per channel.
input bool   InpDdSizingEnabled      = false; // false = keep JSON/override lots
input double InpDdUtilizationPct     = 50.0;  // share of the reservoir at risk
input double InpDdStepPct            = 10.0;  // recompute only every +/- this % of reservoir
input double InpDdMaxLot             = 0.0;   // 0 = off: hard cap per position
input double InpDdFloatIvan          = 39.9;  // measured worst floating per 0.01 lot
input double InpDdFloatStark         = 10.4;
input double InpDdFloatGold          = 15.0;
input double InpDdFloatOro           = 12.4;
input double InpDdFloatForex         = 20.0;
// Concurrent exposure cap across all TG positions. 0 = off.
input double InpMaxConcurrentLots    = 0.0;

//--- trade objects
CTrade         g_trade;
CPositionInfo  g_pos;
CSymbolInfo    g_sym;

//--- channel state
#define MAX_CHANNELS 16
string g_channelSuffix[MAX_CHANNELS];
string g_channelFile[MAX_CHANNELS];
string g_lastTimestamp[MAX_CHANNELS];
int    g_channelCount = 0;

enum EntryRangeDecision
  {
   ENTRY_EXECUTE_IN_RANGE = 0,
   ENTRY_EXECUTE_TOLERANCE = 1,
   ENTRY_CANCELLED = 2
  };

//+------------------------------------------------------------------+
string Trim(const string s)
  {
   string t = s;
   StringTrimLeft(t);
   StringTrimRight(t);
   return t;
  }

//+------------------------------------------------------------------+
string JsonGetString(const string json, const string key)
  {
   string pat = "\"" + key + "\"";
   int p = StringFind(json, pat);
   if(p < 0)
      return "";
   p = StringFind(json, ":", p);
   if(p < 0)
      return "";
   p++;
   while(p < StringLen(json) && (StringGetCharacter(json, p) == ' ' || StringGetCharacter(json, p) == '\t'))
      p++;
   if(StringGetCharacter(json, p) == '"')
     {
      int q1 = p + 1;
      int q2 = StringFind(json, "\"", q1);
      if(q2 < 0)
         return "";
      return StringSubstr(json, q1, q2 - q1);
     }
   int end = p;
   while(end < StringLen(json))
     {
      ushort c = StringGetCharacter(json, end);
      if(c == ',' || c == '}' || c == '\n' || c == '\r')
         break;
      end++;
     }
   return Trim(StringSubstr(json, p, end - p));
  }

//+------------------------------------------------------------------+
double JsonGetNumber(const string json, const string key)
  {
   string v = JsonGetString(json, key);
   if(v == "" || v == "null")
      return 0.0;
   StringReplace(v, ",", ".");
   return StringToDouble(v);
  }

//+------------------------------------------------------------------+
bool JsonGetBool(const string json, const string key)
  {
   string v = JsonGetString(json, key);
   return (v == "true" || v == "1");
  }

//+------------------------------------------------------------------+
int JsonGetInt(const string json, const string key)
  {
   return (int)JsonGetNumber(json, key);
  }

//+------------------------------------------------------------------+
bool JsonGetNumberArray(const string json, const string key, double &out[])
  {
   ArrayResize(out, 0);
   string pat = "\"" + key + "\"";
   int p = StringFind(json, pat);
   if(p < 0)
      return false;
   // The value of this key must itself be an array: on "entry_range": null the
   // next '[' belongs to another key (tp_levels), and reading it would turn TP1/TP2
   // into an entry zone and cancel the signal.
   int colon = StringFind(json, ":", p + StringLen(pat));
   if(colon < 0)
      return false;
   int v = colon + 1;
   int len = StringLen(json);
   while(v < len)
     {
      ushort c = StringGetCharacter(json, v);
      if(c == ' ' || c == '\t' || c == '\r' || c == '\n')
        {
         v++;
         continue;
        }
      break;
     }
   if(v >= len || StringGetCharacter(json, v) != '[')
      return false;
   p = v;
   int q = StringFind(json, "]", p);
   if(q < 0)
      return false;
   string inner = StringSubstr(json, p + 1, q - p - 1);
   if(Trim(inner) == "")
      return true;
   string parts[];
   int n = StringSplit(inner, ',', parts);
   ArrayResize(out, n);
   for(int i = 0; i < n; i++)
     {
      string v = Trim(parts[i]);
      StringReplace(v, ",", ".");
      out[i] = StringToDouble(v);
     }
   return true;
  }

//+------------------------------------------------------------------+
string BuildAbsoluteSignalPath(const string fileName)
  {
   string base = InpSignalsPath;
   if(StringLen(base) > 0 && StringGetCharacter(base, StringLen(base) - 1) != '\\')
      base += "\\";
   return base + fileName;
  }

//+------------------------------------------------------------------+
string BuildRelativeSignalPath(const string fileName)
  {
   if(StringLen(InpSignalsPath) == 0)
      return fileName;
   string base = InpSignalsPath;
   if(StringGetCharacter(base, StringLen(base) - 1) != '\\')
      base += "\\";
   return base + fileName;
  }

//+------------------------------------------------------------------+
bool ReadTextFileContent(const string path, string &content)
  {
   content = "";
   int flags = FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ;
   ResetLastError();
   int h = FileOpen(path, flags);
   if(h == INVALID_HANDLE)
     {
      ResetLastError();
      h = FileOpen(path, flags | FILE_COMMON);
      if(h == INVALID_HANDLE)
         return false;
     }
   while(!FileIsEnding(h))
     {
      content += FileReadString(h);
      if(!FileIsEnding(h))
         content += "\n";
     }
   FileClose(h);
   return true;
  }

//+------------------------------------------------------------------+
string JoinStrings(const string &parts[], const string sep)
  {
   string out = "";
   for(int i = 0; i < ArraySize(parts); i++)
     {
      if(i > 0)
         out += sep;
      out += parts[i];
     }
   return out;
  }

//+------------------------------------------------------------------+
bool ReadSignalFile(const string fileName, string &content)
  {
   string candidates[];
   int n = 0;

   if(InpUseAbsolutePath && StringLen(InpSignalsPath) > 0)
     {
      ArrayResize(candidates, n + 1);
      candidates[n++] = BuildAbsoluteSignalPath(fileName);
     }

   ArrayResize(candidates, n + 1);
   candidates[n++] = BuildRelativeSignalPath(fileName);

   ArrayResize(candidates, n + 1);
   candidates[n++] = fileName;

   for(int i = 0; i < n; i++)
     {
      if(ReadTextFileContent(candidates[i], content))
         return true;
     }

   Print("[TradinGo] FileOpen failed for ", fileName,
         " | tried: ", JoinStrings(candidates, " ; "),
         " | err=", GetLastError(),
         " | hint: bridge writes to MQL5\\Files of THIS terminal");
   return false;
  }

//+------------------------------------------------------------------+
bool WriteTextFileContent(const string path, const string content)
  {
   int flags = FILE_WRITE | FILE_TXT | FILE_ANSI;
   ResetLastError();
   int h = FileOpen(path, flags);
   if(h == INVALID_HANDLE)
     {
      ResetLastError();
      h = FileOpen(path, flags | FILE_COMMON);
      if(h == INVALID_HANDLE)
         return false;
     }
   FileWriteString(h, content);
   FileClose(h);
   return true;
  }

//+------------------------------------------------------------------+
bool ClearSignalFile(const string fileName)
  {
   string payload = "{\"action\":\"NONE\"}\n";
   string candidates[];
   int n = 0;

   if(InpUseAbsolutePath && StringLen(InpSignalsPath) > 0)
     {
      ArrayResize(candidates, n + 1);
      candidates[n++] = BuildAbsoluteSignalPath(fileName);
     }

   ArrayResize(candidates, n + 1);
   candidates[n++] = BuildRelativeSignalPath(fileName);

   ArrayResize(candidates, n + 1);
   candidates[n++] = fileName;

   for(int i = 0; i < n; i++)
     {
      if(WriteTextFileContent(candidates[i], payload))
        {
         Print("[TradinGo] Cleared ", fileName, " -> NONE (path=", candidates[i], ")");
         return true;
        }
     }
   Print("[TradinGo] Clear failed for ", fileName, " err=", GetLastError());
   return false;
  }

//+------------------------------------------------------------------+
string ResolveSymbol(const string raw)
  {
   string s = raw;
   StringToUpper(s);
   if(StringLen(InpSymbolSuffix) > 0 && StringFind(s, InpSymbolSuffix) < 0)
      s += InpSymbolSuffix;
   if(!SymbolSelect(s, true))
      Print("[TradinGo] SymbolSelect warning: ", s);
   return s;
  }

//+------------------------------------------------------------------+
ulong TradeMagic(const int magicBase, const int index)
  {
   return (ulong)magicBase + (ulong)index + InpMagicOffset;
  }

//+------------------------------------------------------------------+
bool IsOurPosition(const ulong ticket, const int magicBase, const int maxTrades)
  {
   if(!g_pos.SelectByTicket(ticket))
      return false;
   ulong mg = g_pos.Magic();
   for(int i = 1; i <= maxTrades; i++)
     {
      if(mg == TradeMagic(magicBase, i))
         return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
ulong FindOurPositionByMagic(const string symbol, const ulong magic)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(g_pos.Symbol() != symbol)
         continue;
      if((ulong)g_pos.Magic() == magic)
         return g_pos.Ticket();
     }
   return 0;
  }

//+------------------------------------------------------------------+
int CountOurPositions(const string symbol, const int magicBase, const int maxTrades)
  {
   int cnt = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(g_pos.Symbol() != symbol)
         continue;
      if(IsOurPosition(g_pos.Ticket(), magicBase, maxTrades))
         cnt++;
     }
   return cnt;
  }

//+------------------------------------------------------------------+
//| CTrade keeps the magic of the last order it sent, so an exit deal |
//| inherits the magic of the last opened position and lands on the   |
//| wrong channel in every per-magic report. Always close with the    |
//| magic of the position itself.                                     |
//+------------------------------------------------------------------+
bool ClosePositionKeepMagic(const ulong ticket)
  {
   if(!g_pos.SelectByTicket(ticket))
      return false;
   g_trade.SetExpertMagicNumber((int)g_pos.Magic());
   return g_trade.PositionClose(ticket);
  }

//+------------------------------------------------------------------+
bool ClosePartialKeepMagic(const ulong ticket, const double volume)
  {
   if(!g_pos.SelectByTicket(ticket))
      return false;
   g_trade.SetExpertMagicNumber((int)g_pos.Magic());
   return g_trade.PositionClosePartial(ticket, volume);
  }

//+------------------------------------------------------------------+
void CloseOurPositions(const string symbol, const int magicBase, const int maxTrades)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(g_pos.Symbol() != symbol)
         continue;
      if(!IsOurPosition(g_pos.Ticket(), magicBase, maxTrades))
         continue;
      ClosePositionKeepMagic(g_pos.Ticket());
     }
  }

//+------------------------------------------------------------------+
double NormalizeLot(const string symbol, double lot)
  {
   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step   = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0)
      step = 0.01;
   lot *= InpLotMultiplier;
   lot = MathFloor(lot / step) * step;
   if(lot < minLot)
      lot = minLot;
   if(lot > maxLot)
      lot = maxLot;
   return lot;
  }

//+------------------------------------------------------------------+
int RangeToleranceForChannel(const string channelFile, const string json)
  {
   string ch = JsonGetString(json, "channel_id");
   StringToUpper(ch);
   string fileLower = channelFile;
   StringToLower(fileLower);
   bool isOro = (ch == "CH_ORO" || StringFind(fileLower, "oro") >= 0);
   if(isOro && InpOroRangeTolerancePoints > 0)
      return InpOroRangeTolerancePoints;
   return InpRangeTolerancePoints;
  }

//+------------------------------------------------------------------+
void AdjustStopsToMinDistance(const string symbol, const string direction,
                              double &sl, double &tp, const int extraPts = -1)
  {
   // Avoid MT5 retcode 10016 (invalid stops) by enforcing SYMBOL_TRADE_STOPS_LEVEL
   // and forcing SL/TP onto the legal side of the current price.
   g_sym.Name(symbol);
   g_sym.RefreshRates();
   double point = g_sym.Point();
   if(point <= 0.0)
      point = _Point;
   int stopsLevel = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
   int freezeLevel = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   int bufferPts = (extraPts >= 0) ? extraPts : InpStopBufferPoints;
   if(bufferPts < 0)
      bufferPts = 0;
   int minPts = MathMax(stopsLevel, freezeLevel) + bufferPts;
   if(minPts < 1)
      minPts = 1;
   double minDist = minPts * point;
   int digits = (int)g_sym.Digits();
   // For modifying protective SL use the price that MT5 validates against.
   double price = (direction == "BUY") ? g_sym.Bid() : g_sym.Ask();

   if(direction == "BUY")
     {
      if(sl > 0.0 && sl > price - minDist)
         sl = NormalizeDouble(price - minDist, digits);
      if(tp > 0.0 && tp < price + minDist)
         tp = NormalizeDouble(price + minDist, digits);
     }
   else
     {
      if(sl > 0.0 && sl < price + minDist)
         sl = NormalizeDouble(price + minDist, digits);
      if(tp > 0.0 && tp > price - minDist)
         tp = NormalizeDouble(price - minDist, digits);
     }
  }

//+------------------------------------------------------------------+
bool ModifyPositionSLTP(const ulong ticket, const double sl, const double tp,
                        const int extraPts = -1)
  {
   if(!g_pos.SelectByTicket(ticket))
      return false;
   string symbol = g_pos.Symbol();
   string direction = (g_pos.PositionType() == POSITION_TYPE_BUY) ? "BUY" : "SELL";
   double curSl = g_pos.StopLoss();
   double curTp = g_pos.TakeProfit();
   double nsl = sl > 0 ? sl : curSl;
   double ntp = tp > 0 ? tp : curTp;
   if(nsl > 0.0 || ntp > 0.0)
      AdjustStopsToMinDistance(symbol, direction, nsl, ntp, extraPts);
   if(nsl == curSl && ntp == curTp)
      return true;
   bool ok = g_trade.PositionModify(ticket, nsl, ntp);
   if(!ok)
      Print("[TradinGo] PositionModify failed ticket=", ticket,
            " err=", g_trade.ResultRetcode(),
            " sl=", DoubleToString(nsl, (int)g_sym.Digits()),
            " tp=", DoubleToString(ntp, (int)g_sym.Digits()));
   return ok;
  }

//+------------------------------------------------------------------+
ulong g_bePending[];

bool BePendingContains(const ulong ticket)
  {
   for(int i = 0; i < ArraySize(g_bePending); i++)
      if(g_bePending[i] == ticket)
         return true;
   return false;
  }

void BePendingAdd(const ulong ticket)
  {
   if(BePendingContains(ticket))
      return;
   int n = ArraySize(g_bePending);
   ArrayResize(g_bePending, n + 1);
   g_bePending[n] = ticket;
  }

void BePendingRemove(const int index)
  {
   int n = ArraySize(g_bePending);
   for(int i = index; i < n - 1; i++)
      g_bePending[i] = g_bePending[i + 1];
   ArrayResize(g_bePending, n - 1);
  }

bool ApplyBreakEvenSLEx(const ulong ticket, const bool fromQueue);

bool ApplyBreakEvenSL(const ulong ticket)
  {
   return ApplyBreakEvenSLEx(ticket, false);
  }

datetime g_beLastRetry = 0;

void ProcessBePending()
  {
   if(ArraySize(g_bePending) == 0 || TimeCurrent() == g_beLastRetry)
      return;
   g_beLastRetry = TimeCurrent();
   for(int i = ArraySize(g_bePending) - 1; i >= 0; i--)
     {
      ulong tk = g_bePending[i];
      if(!g_pos.SelectByTicket(tk))
        {
         BePendingRemove(i);
         continue;
        }
      if(ApplyBreakEvenSLEx(tk, true))
        {
         Print("[TradinGo] BE_PENDING_APPLIED ticket=", tk,
               " sl=", DoubleToString(g_pos.PriceOpen(), (int)g_sym.Digits()));
         BePendingRemove(i);
        }
     }
  }

bool ApplyBreakEvenSLEx(const ulong ticket, const bool fromQueue)
  {
   // Close-half / CHECK_AND_BE: prefer SL=entry; if invalid vs market, clamp to min legal stop.
   // Retry with a wider buffer on 10016 (spread race after half-close).
   if(!g_pos.SelectByTicket(ticket))
      return false;
   string symbol = g_pos.Symbol();
   string direction = (g_pos.PositionType() == POSITION_TYPE_BUY) ? "BUY" : "SELL";
   double be = g_pos.PriceOpen();
   int buffers[3];
   buffers[0] = InpStopBufferPoints;
   buffers[1] = InpStopBufferPoints + 20;
   buffers[2] = InpStopBufferPoints + 50;
   for(int attempt = 0; attempt < 3; attempt++)
     {
      if(!g_pos.SelectByTicket(ticket))
         return false;
      double nsl = be;
      double ntp = g_pos.TakeProfit();
      AdjustStopsToMinDistance(symbol, direction, nsl, ntp, buffers[attempt]);
      double tol = g_sym.Point();
      bool worseThanEntry = (direction == "BUY") ? (nsl < be - tol) : (nsl > be + tol);
      if(InpBeNeverWorseThanEntry && worseThanEntry)
        {
         if(fromQueue)
            return false;
         if(InpBePendingRetry)
           {
            BePendingAdd(ticket);
            Print("[TradinGo] BE_PENDING ticket=", ticket,
                  " entry=", DoubleToString(be, (int)g_sym.Digits()),
                  " would_be_sl=", DoubleToString(nsl, (int)g_sym.Digits()),
                  " (", direction, ") — retry until SL=entry is legal");
           }
         else
            Print("[TradinGo] BE_SKIPPED_WORSE_THAN_ENTRY ticket=", ticket,
                  " entry=", DoubleToString(be, (int)g_sym.Digits()),
                  " would_be_sl=", DoubleToString(nsl, (int)g_sym.Digits()),
                  " (", direction, ") — SL kept unchanged");
         return false;
        }
      if(MathAbs(nsl - be) > g_sym.Point())
         Print("[TradinGo] BE clamped ticket=", ticket,
               " entry=", DoubleToString(be, (int)g_sym.Digits()),
               " -> sl=", DoubleToString(nsl, (int)g_sym.Digits()),
               " (", direction, ") bufPts=", buffers[attempt]);
      if(ModifyPositionSLTP(ticket, nsl, 0, buffers[attempt]))
         return true;
      if(g_trade.ResultRetcode() != 10016)
         return false;
     }
   return false;
  }

//+------------------------------------------------------------------+
string ChannelKeyFromFile(const string channelFile, const string json)
  {
   string f = channelFile;
   StringToLower(f);
   StringReplace(f, "signal_ch_", "");
   StringReplace(f, ".json", "");
   if(f != "")
      return f;
   string cid = JsonGetString(json, "channel_id");
   StringToLower(cid);
   StringReplace(cid, "ch_", "");
   return cid;
  }

//+------------------------------------------------------------------+
string ChannelShortTag(const string channelFile, const string json)
  {
   string key = ChannelKeyFromFile(channelFile, json);
   if(key == "ivan" && InpTagIvan != "")
      return InpTagIvan;
   if(key == "stark" && InpTagStark != "")
      return InpTagStark;
   if(key == "gold" && InpTagGold != "")
      return InpTagGold;
   if(key == "oro" && InpTagOro != "")
      return InpTagOro;
   if(key == "forex" && InpTagForex != "")
      return InpTagForex;

   string cid = JsonGetString(json, "channel_id");
   if(cid != "")
     {
      StringReplace(cid, "CH_", "");
      return cid;
     }
   string f = channelFile;
   StringReplace(f, "signal_ch_", "");
   StringReplace(f, ".json", "");
   StringToUpper(f);
   return f;
  }

//+------------------------------------------------------------------+
double LotOverrideForChannel(const string channelFile, const string json)
  {
   string key = ChannelKeyFromFile(channelFile, json);
   if(key == "ivan")
      return InpLotIvan;
   if(key == "stark")
      return InpLotStark;
   if(key == "gold")
      return InpLotGold;
   if(key == "oro")
      return InpLotOro;
   if(key == "forex")
      return InpLotForex;
   return 0.0;
  }

//+------------------------------------------------------------------+
string BuildTradeComment(const string channelTag, const int tpIndex, const string json = "")
  {
   string base;
   if(InpCommentUseTgPrefix)
     {
      if(tpIndex > 0)
         base = StringFormat("TG-%s-T%d", channelTag, tpIndex);
      else
         base = StringFormat("TG-%s", channelTag);
     }
   else
     {
      // Compact Moneta style: IT-T1 / AS-T1
      if(tpIndex > 0)
         base = StringFormat("%s-T%d", channelTag, tpIndex);
      else
         base = channelTag;
     }
   // Append signal_id (truncated to fit MT5's 31-char comment limit) for journal joins.
   string sid = (json == "") ? "" : JsonGetString(json, "signal_id");
   if(sid != "")
     {
      string cand = base + "-" + sid;
      if(StringLen(cand) > 31)
         cand = StringSubstr(cand, 0, 31);
      return cand;
     }
   return base;
  }

//+------------------------------------------------------------------+
void AppendSignalStat(const string channelFile, const string json,
                      const string symbol, const string direction,
                      const string status,
                      const double rangeLo, const double rangeHi,
                      const double signalEntry, const double fillPrice,
                      const double distancePoints, const int tpIndex,
                      const ulong magic)
  {
   if(!InpLogCancelledSignals)
      return;

   string ch = JsonGetString(json, "channel_id");
   string ts = JsonGetString(json, "timestamp");
   double slippagePts = 0.0;
   if(signalEntry > 0.0 && fillPrice > 0.0)
     {
      g_sym.Name(symbol);
      double point = g_sym.Point();
      if(point <= 0.0)
         point = _Point;
      slippagePts = (fillPrice - signalEntry) / point;
      if(direction == "SELL")
         slippagePts = -slippagePts;
     }

   string line = StringFormat(
      "%s,%s,%s,%s,%s,%s,%.5f,%.5f,%.5f,%.5f,%.1f,%.1f,%d,%s,%s\n",
      TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
      ch,
      channelFile,
      symbol,
      direction,
      status,
      signalEntry,
      rangeLo,
      rangeHi,
      fillPrice,
      slippagePts,
      distancePoints,
      tpIndex,
      IntegerToString(magic),
      ts
   );

   int h = FileOpen("tradingo_signal_stats.csv",
                    FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
     {
      h = FileOpen("tradingo_signal_stats.csv",
                   FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_WRITE);
      if(h != INVALID_HANDLE)
         FileWriteString(h,
            "time,channel_id,channel_file,symbol,direction,status,signal_entry,range_lo,range_hi,fill_price,slippage_points,distance_points,tp_index,magic,telegram_ts\n");
     }
   if(h != INVALID_HANDLE)
     {
      FileSeek(h, 0, SEEK_END);
      FileWriteString(h, line);
      FileClose(h);
     }
  }

//+------------------------------------------------------------------+
double SignalEntryFromJson(const string json, const double rangeLo, const double rangeHi)
  {
   double entry = JsonGetNumber(json, "entry");
   if(entry > 0.0)
      return entry;
   if(rangeHi > rangeLo)
      return (rangeLo + rangeHi) / 2.0;
   return 0.0;
  }

//+------------------------------------------------------------------+
bool StopsOnCorrectSide(const string direction, const double price,
                        const double sl, const double tp)
  {
   if(direction == "BUY")
     {
      if(sl > 0.0 && sl >= price)
         return false;
      if(tp > 0.0 && tp <= price)
         return false;
     }
   else
     {
      if(sl > 0.0 && sl <= price)
         return false;
      if(tp > 0.0 && tp >= price)
         return false;
     }
   return true;
  }

//+------------------------------------------------------------------+
//| Off-market guard: the channel sometimes posts levels of another   |
//| price context ("sell 4039 tp 4030" with gold at 4237). Without    |
//| this check the stops get clamped to the legal side and the        |
//| position opens only to be closed at once, paying the spread.      |
//+------------------------------------------------------------------+
bool LevelsNearMarket(const string symbol, const double price,
                      const double sl, const double tp, const double entry,
                      double &outWorstPct)
  {
   outWorstPct = 0.0;
   if(InpMaxLevelDeviationPct <= 0.0 || price <= 0.0)
      return true;
   double levels[3];
   levels[0] = sl;
   levels[1] = tp;
   levels[2] = entry;
   for(int i = 0; i < 3; i++)
     {
      if(levels[i] <= 0.0)
         continue;
      double devPct = MathAbs(levels[i] - price) / price * 100.0;
      if(devPct > outWorstPct)
         outWorstPct = devPct;
     }
   return (outWorstPct <= InpMaxLevelDeviationPct);
  }

//+------------------------------------------------------------------+
bool OpenMarket(const string symbol, const string direction, const double lot,
                const double sl, const double tp, const ulong magic,
                const string comment,
                const string channelFile, const string json,
                const string entryStatus,
                const double rangeLo, const double rangeHi,
                const double distancePoints, const int tpIndex)
  {
   // Concurrent exposure cap: evaluated per position so the TPs already
   // opened for this signal stay, and only the excess volume is rejected.
   if(!ExposureAllows(lot))
     {
      AppendSignalStat(channelFile, json, symbol, direction, "CANCELLED_LOTS_CAP",
                       rangeLo, rangeHi, SignalEntryFromJson(json, rangeLo, rangeHi),
                       0.0, distancePoints, tpIndex, magic);
      return false;
     }
   g_sym.Name(symbol);
   g_sym.RefreshRates();
   g_trade.SetExpertMagicNumber((int)magic);
   g_trade.SetDeviationInPoints(InpMaxSlippagePoints);
   double nsl = sl;
   double ntp = tp;
   if(nsl > 0.0 || ntp > 0.0)
      AdjustStopsToMinDistance(symbol, direction, nsl, ntp);
   double price = (direction == "BUY") ? g_sym.Ask() : g_sym.Bid();
   double devPct = 0.0;
   if(!LevelsNearMarket(symbol, price,
                        sl, tp, SignalEntryFromJson(json, rangeLo, rangeHi), devPct))
     {
      Print("[TradinGo] Open skipped ", symbol, " ", direction,
            " off-market levels price=", DoubleToString(price, (int)g_sym.Digits()),
            " sl=", DoubleToString(sl, (int)g_sym.Digits()),
            " tp=", DoubleToString(tp, (int)g_sym.Digits()),
            " deviation=", DoubleToString(devPct, 2), "%",
            " max=", DoubleToString(InpMaxLevelDeviationPct, 2), "%");
      AppendSignalStat(channelFile, json, symbol, direction, "CANCELLED_OFF_MARKET",
                       rangeLo, rangeHi, SignalEntryFromJson(json, rangeLo, rangeHi),
                       0.0, distancePoints, tpIndex, magic);
      return false;
     }
   if((nsl > 0.0 || ntp > 0.0) && !StopsOnCorrectSide(direction, price, nsl, ntp))
     {
      Print("[TradinGo] Open skipped ", symbol, " ", direction,
            " invalid stop side price=", DoubleToString(price, (int)g_sym.Digits()),
            " sl=", DoubleToString(nsl, (int)g_sym.Digits()),
            " tp=", DoubleToString(ntp, (int)g_sym.Digits()),
            " (stale or incoherent signal)");
      return false;
     }
   bool ok = false;
   if(direction == "BUY")
      ok = g_trade.Buy(lot, symbol, 0.0, nsl > 0 ? nsl : 0.0, ntp > 0 ? ntp : 0.0, comment);
   else
      ok = g_trade.Sell(lot, symbol, 0.0, nsl > 0 ? nsl : 0.0, ntp > 0 ? ntp : 0.0, comment);
   if(!ok)
     {
      Print("[TradinGo] Open failed ", symbol, " ", direction, " err=", g_trade.ResultRetcode());
      return false;
     }
   double fillPrice = g_trade.ResultPrice();
   if(fillPrice <= 0.0)
      fillPrice = (direction == "BUY") ? g_sym.Ask() : g_sym.Bid();
   double signalEntry = SignalEntryFromJson(json, rangeLo, rangeHi);
   AppendSignalStat(channelFile, json, symbol, direction, entryStatus,
                    rangeLo, rangeHi, signalEntry, fillPrice, distancePoints,
                    tpIndex, magic);
   Print("[TradinGo] Opened ", comment, " ", symbol, " ", direction,
         " lot=", DoubleToString(lot, 2),
         " fill=", DoubleToString(fillPrice, (int)g_sym.Digits()),
         " status=", entryStatus);
   // --- Artefact B/C: trade journal tracking + market context on first fill ---
   double point = g_sym.Point();
   if(point <= 0.0)
      point = _Point;
   double slipPts = 0.0;
   if(signalEntry > 0.0 && fillPrice > 0.0)
     {
      slipPts = (fillPrice - signalEntry) / point;
      if(direction == "SELL")
         slipPts = -slipPts;
     }
   ulong posId = 0;
   ulong deal = g_trade.ResultDeal();
   if(deal > 0 && HistoryDealSelect(deal))
      posId = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   if(posId == 0)
      posId = g_trade.ResultOrder();
   string sid = JsonGetString(json, "signal_id");
   string chTag = ChannelShortTag(channelFile, json);
   TrackAddOpen(posId, sid, chTag, tpIndex, magic, symbol, direction,
                signalEntry, rangeLo, rangeHi, sl, tp, lot, fillPrice, slipPts,
                (datetime)TimeCurrent());
   WriteMarketContext(symbol, sid);
   return true;
  }

//+------------------------------------------------------------------+
bool PriceInRange(const string symbol, const string direction, const double lo, const double hi)
  {
   g_sym.Name(symbol);
   g_sym.RefreshRates();
   double px = (direction == "BUY") ? g_sym.Ask() : g_sym.Bid();
   return (px >= lo && px <= hi);
  }

//+------------------------------------------------------------------+
EntryRangeDecision EvaluateEntryRange(const string symbol, const string direction,
                                      const double lo, const double hi,
                                      const int maxTolerancePoints,
                                      double &outPrice, double &outDistancePoints)
  {
   g_sym.Name(symbol);
   g_sym.RefreshRates();
   outPrice = (direction == "BUY") ? g_sym.Ask() : g_sym.Bid();
   if(outPrice >= lo && outPrice <= hi)
     {
      outDistancePoints = 0.0;
      return ENTRY_EXECUTE_IN_RANGE;
     }
   double distPrice = (outPrice < lo) ? (lo - outPrice) : (outPrice - hi);
   double point = g_sym.Point();
   if(point <= 0.0)
      point = _Point;
   outDistancePoints = distPrice / point;
   if(outDistancePoints <= (double)maxTolerancePoints)
      return ENTRY_EXECUTE_TOLERANCE;
   return ENTRY_CANCELLED;
  }

//+------------------------------------------------------------------+
void LogCancelledSignal(const string channelFile, const string json,
                        const string symbol, const string direction,
                        const double lo, const double hi,
                        const double price, const double distancePoints,
                        const int maxTolerancePoints)
  {
   Print("[TradinGo] SIGNAL_CANCELLED ", JsonGetString(json, "channel_id"), " ", symbol, " ", direction,
         " range=[", DoubleToString(lo, (int)g_sym.Digits()), ",",
         DoubleToString(hi, (int)g_sym.Digits()), "]",
         " price=", DoubleToString(price, (int)g_sym.Digits()),
         " distance_points=", DoubleToString(distancePoints, 1),
         " max_tolerance=", maxTolerancePoints,
         " ts=", JsonGetString(json, "timestamp"));
   AppendSignalStat(channelFile, json, symbol, direction, "CANCELLED_RANGE",
                    lo, hi, SignalEntryFromJson(json, lo, hi), price,
                    distancePoints, 0, 0);
  }

//+------------------------------------------------------------------+
bool TryExecuteWithEntryRange(const string channelFile, const string json,
                              const string symbol, const string direction,
                              const double lo, const double hi,
                              string &outEntryStatus, double &outDistancePoints)
  {
   double price = 0.0;
   outDistancePoints = 0.0;
   int maxTol = RangeToleranceForChannel(channelFile, json);
   EntryRangeDecision decision = EvaluateEntryRange(symbol, direction, lo, hi, maxTol,
                                                    price, outDistancePoints);
   if(decision == ENTRY_EXECUTE_IN_RANGE)
     {
      outEntryStatus = "EXECUTED_IN_RANGE";
      Print("[TradinGo] ENTRY_IN_RANGE ", symbol, " ", direction,
            " price=", DoubleToString(price, (int)g_sym.Digits()),
            " range=[", DoubleToString(lo, (int)g_sym.Digits()), ",",
            DoubleToString(hi, (int)g_sym.Digits()), "]");
      return true;
     }
   if(decision == ENTRY_EXECUTE_TOLERANCE)
     {
      outEntryStatus = "EXECUTED_TOLERANCE";
      Print("[TradinGo] ENTRY_TOLERANCE ", symbol, " ", direction,
            " price=", DoubleToString(price, (int)g_sym.Digits()),
            " distance_points=", DoubleToString(outDistancePoints, 1),
            " max=", maxTol);
      return true;
     }
   LogCancelledSignal(channelFile, json, symbol, direction, lo, hi, price,
                      outDistancePoints, maxTol);
   outEntryStatus = "CANCELLED_RANGE";
   return false;
  }

//+------------------------------------------------------------------+
// A re-entry inherits the SL of the original setup: if the market already ate
// part of that stop distance, the trade opens with a fraction of the published
// risk budget (in production a re-entry filled 3.75 away from the signal entry
// with the SL only 4.75 away, and it was stopped two minutes later).
bool ReentryDriftAllows(const string channelFile, const string json,
                        const string symbol, const string direction,
                        const double entry, const double sl)
  {
   if(InpReentryMaxDriftPctOfSl <= 0.0)
      return true;
   if(!JsonGetBool(json, "allow_stack"))
      return true;
   if(entry <= 0.0 || sl <= 0.0)
      return true;
   double slDistance = MathAbs(entry - sl);
   if(slDistance <= 0.0)
      return true;
   g_sym.Name(symbol);
   g_sym.RefreshRates();
   double price = (direction == "BUY") ? g_sym.Ask() : g_sym.Bid();
   if(price <= 0.0)
      return true;
   double driftPct = MathAbs(price - entry) / slDistance * 100.0;
   if(driftPct <= InpReentryMaxDriftPctOfSl)
      return true;
   Print("[TradinGo] REENTRY_CANCELLED ", JsonGetString(json, "channel_id"), " ",
         symbol, " ", direction,
         " entry=", DoubleToString(entry, (int)g_sym.Digits()),
         " price=", DoubleToString(price, (int)g_sym.Digits()),
         " sl=", DoubleToString(sl, (int)g_sym.Digits()),
         " drift=", DoubleToString(driftPct, 1), "% of SL distance",
         " max=", DoubleToString(InpReentryMaxDriftPctOfSl, 1), "%");
   AppendSignalStat(channelFile, json, symbol, direction, "CANCELLED_REENTRY_DRIFT",
                    0, 0, entry, price, 0.0, 0, 0);
   return false;
  }

//+------------------------------------------------------------------+
bool OpenSplitTrades(const string symbol, const string direction,
                     const double lot, const double sl, const double &tps[],
                     const int magicBase,
                     const string channelFile, const string json,
                     const string entryStatus,
                     const double rangeLo, const double rangeHi,
                     const double distancePoints)
  {
   string channelTag = ChannelShortTag(channelFile, json);
   int n = ArraySize(tps);
   if(n <= 0)
      n = 1;
   bool any = false;
   for(int i = 0; i < n; i++)
     {
      double tp = (i < ArraySize(tps)) ? tps[i] : 0.0;
      ulong magic = TradeMagic(magicBase, i + 1);
      string comment = BuildTradeComment(channelTag, i + 1, json);
      if(OpenMarket(symbol, direction, lot, sl, tp, magic, comment,
                    channelFile, json, entryStatus, rangeLo, rangeHi,
                    distancePoints, i + 1))
         any = true;
     }
   return any;
  }

//+------------------------------------------------------------------+
// Drop levels that would hurt an already open position: a TP behind its open
// price (the position would be "taken profit" at a loss, as happened when a new
// GOLD setup with lower TPs reached a BUY opened higher) and an SL the market
// has already crossed (instant liquidation). 0 = leave the current level.
void FilterAdverseLevels(const ulong ticket, double &sl, double &tp)
  {
   if(!g_pos.SelectByTicket(ticket))
      return;
   string symbol = g_pos.Symbol();
   bool isBuy = (g_pos.PositionType() == POSITION_TYPE_BUY);
   double openPrice = g_pos.PriceOpen();
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);

   if(tp > 0.0)
     {
      bool tpProfitable = isBuy ? (tp > openPrice) : (tp < openPrice);
      if(!tpProfitable)
        {
         Print("[TradinGo] MODIFY_SKIPPED_ADVERSE_TP ticket=", ticket,
               " tp=", DoubleToString(tp, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
               " open=", DoubleToString(openPrice, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
               " side=", (isBuy ? "BUY" : "SELL"), " — TP kept unchanged");
         tp = 0.0;
        }
     }

   if(sl > 0.0)
     {
      bool slCrossed = isBuy ? (sl >= bid) : (sl <= ask);
      if(slCrossed)
        {
         Print("[TradinGo] MODIFY_SKIPPED_CROSSED_SL ticket=", ticket,
               " sl=", DoubleToString(sl, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
               " bid=", DoubleToString(bid, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
               " ask=", DoubleToString(ask, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
               " side=", (isBuy ? "BUY" : "SELL"), " — SL kept unchanged");
         sl = 0.0;
        }
     }
  }

//+------------------------------------------------------------------+
bool ModifyExistingTrades(const string symbol, const int magicBase,
                          const double sl, const double &tps[])
  {
   int idx = 0;
   bool any = false;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(g_pos.Symbol() != symbol)
         continue;
      if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
         continue;
      double tp = (idx < ArraySize(tps)) ? tps[idx] : 0.0;
      double useSl = sl;
      double useTp = tp;
      if(InpProtectExistingLevels)
         FilterAdverseLevels(g_pos.Ticket(), useSl, useTp);
      if(ModifyPositionSLTP(g_pos.Ticket(), useSl, useTp))
         any = true;
      idx++;
     }
   return any;
  }

//+------------------------------------------------------------------+
// Provisional SL for a naked open, so no ticket ever sits unprotected while
// waiting for the message with the real levels. 0 when the guard is off.
double NakedFallbackSl(const string symbol, const string direction)
  {
   if(InpNakedFallbackSlPoints <= 0)
      return 0.0;
   g_sym.Name(symbol);
   g_sym.RefreshRates();
   double point = g_sym.Point();
   if(point <= 0.0)
      point = _Point;
   double price = (direction == "BUY") ? g_sym.Ask() : g_sym.Bid();
   if(price <= 0.0)
      return 0.0;
   double dist = InpNakedFallbackSlPoints * point;
   double sl = (direction == "BUY") ? price - dist : price + dist;
   sl = NormalizeDouble(sl, (int)g_sym.Digits());
   Print("[TradinGo] NAKED_FALLBACK_SL ", symbol, " ", direction,
         " price=", DoubleToString(price, (int)g_sym.Digits()),
         " sl=", DoubleToString(sl, (int)g_sym.Digits()),
         " pts=", InpNakedFallbackSlPoints);
   return sl;
  }

//+------------------------------------------------------------------+
bool HandleOpen(const string channelFile, const string json)
  {
   string action = JsonGetString(json, "action");
   string direction = JsonGetString(json, "direction");
   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   // Block new opens when bridge heartbeat is stale or the bucket is in killswitch cooldown.
   if(IsOpenBlocked(symbol))
      return false;
   int magicBase = JsonGetInt(json, "magic_base");
   double sl = JsonGetNumber(json, "sl");
   double tps[];
   JsonGetNumberArray(json, "tp_levels", tps);
   int trades = JsonGetInt(json, "trades");
   if(trades <= 0)
      trades = MathMax(1, ArraySize(tps));
   double fixedLot = JsonGetNumber(json, "fixed_lot");
   if(fixedLot <= 0)
      fixedLot = 0.20;
   double ov = LotOverrideForChannel(channelFile, json);
   if(ov > 0.0)
      fixedLot = ov;
   fixedLot = DdSizedLot(symbol, ChannelKeyFromFile(channelFile, json), fixedLot);
   if(fixedLot <= 0.0)
      return false;
   double lot = NormalizeLot(symbol, fixedLot);
   if(JsonGetBool(json, "is_add_signal"))
      lot = NormalizeLot(symbol, fixedLot * InpAddLotFactor);

   if(JsonGetBool(json, "inherit_from_first"))
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         if(!g_pos.SelectByIndex(i))
            continue;
         if(g_pos.Symbol() != symbol)
            continue;
         if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
            continue;
         if(sl <= 0)
            sl = g_pos.StopLoss();
         if(ArraySize(tps) == 0)
           {
            double tp = g_pos.TakeProfit();
            ArrayResize(tps, 1);
            tps[0] = tp;
           }
         break;
        }
     }

   double rangeLo = 0, rangeHi = 0;
   double entryRange[];
   if(JsonGetNumberArray(json, "entry_range", entryRange) && ArraySize(entryRange) >= 2)
     {
      rangeLo = MathMin(entryRange[0], entryRange[1]);
      rangeHi = MathMax(entryRange[0], entryRange[1]);
     }

   string entryStatus = "EXECUTED_DIRECT";
   double distPoints = 0.0;
   if(rangeHi > rangeLo)
     {
      if(!TryExecuteWithEntryRange(channelFile, json, symbol, direction, rangeLo, rangeHi,
                                   entryStatus, distPoints))
         return true;
     }

   if(action == "OPEN_NOW")
     {
      // Naked open: open `trades` market tickets (GOLD expected=2 → TP1/TP2 magic),
      // SL/TP arrive later via UPDATE_OPEN without close/reopen.
      int n = trades;
      if(n <= 0)
         n = 1;
      if(n > 5)
         n = 5;
      double emptyTps[];
      ArrayResize(emptyTps, n);
      for(int i = 0; i < n; i++)
         emptyTps[i] = 0.0;
      double nakedSl = NakedFallbackSl(symbol, direction);
      return OpenSplitTrades(symbol, direction, lot, nakedSl, emptyTps, magicBase,
                             channelFile, json, "EXECUTED_OPEN_NOW", rangeLo, rangeHi,
                             distPoints);
     }

   int openCnt = CountOurPositions(symbol, magicBase, 5);
   if(openCnt > 0)
     {
      // Stack only when bridge explicitly sets allow_stack (intentional re-entry).
      // MSG+EDIT of the same Telegram signal must NOT open a second set of trades.
      bool allowStack = InpStackOpensIfFlatBusy && JsonGetBool(json, "allow_stack");
      if(!allowStack)
        {
         Print("[TradinGo] OPEN skipped — ", openCnt,
               " position(s) exist, modifying SL/TP instead",
               " (allow_stack=", (JsonGetBool(json, "allow_stack") ? "true" : "false"), ")");
         return ModifyExistingTrades(symbol, magicBase, sl, tps);
        }
      Print("[TradinGo] OPEN stack — ", openCnt,
            " position(s) already open, opening additional trade(s)");
     }

   if(!ReentryDriftAllows(channelFile, json, symbol, direction,
                          JsonGetNumber(json, "entry"), sl))
      return true;

   return OpenSplitTrades(symbol, direction, lot, sl, tps, magicBase,
                          channelFile, json, entryStatus, rangeLo, rangeHi, distPoints);
  }

//+------------------------------------------------------------------+
bool HandleUpdateOpen(const string channelFile, const string json)
  {
   string direction = JsonGetString(json, "direction");
   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   int magicBase = JsonGetInt(json, "magic_base");
   double sl = JsonGetNumber(json, "sl");
   double tps[];
   JsonGetNumberArray(json, "tp_levels", tps);
   int trades = JsonGetInt(json, "trades");
   if(trades <= 0)
      trades = MathMax(1, ArraySize(tps));
   double fixedLot = JsonGetNumber(json, "fixed_lot");
   if(fixedLot <= 0)
      fixedLot = 0.10;
   double ovUpd = LotOverrideForChannel(channelFile, json);
   if(ovUpd > 0.0)
      fixedLot = ovUpd;
   double sized = DdSizedLot(symbol, ChannelKeyFromFile(channelFile, json), fixedLot);
   if(sized > 0.0)
      fixedLot = sized;
   double lot = NormalizeLot(symbol, fixedLot);

   int openCnt = CountOurPositions(symbol, magicBase, 5);
   if(openCnt == 0)
     {
      // levels_only: the bridge salvaged SL/TP from a message whose entry zone
      // was unusable (channel typo), so they may only modify open positions.
      if(JsonGetBool(json, "levels_only"))
        {
         Print("[TradinGo] UPDATE_OPEN levels_only and no positions — nothing to do");
         return true;
        }
      double rangeLo = 0, rangeHi = 0;
      double entryRange[];
      string entryStatus = "EXECUTED_DIRECT";
      double distPoints = 0.0;
      if(JsonGetNumberArray(json, "entry_range", entryRange) && ArraySize(entryRange) >= 2)
        {
         rangeLo = MathMin(entryRange[0], entryRange[1]);
         rangeHi = MathMax(entryRange[0], entryRange[1]);
         if(!TryExecuteWithEntryRange(channelFile, json, symbol, direction, rangeLo, rangeHi,
                                      entryStatus, distPoints))
            return true;
        }
      if(IsOpenBlocked(symbol))
         return false;
      Print("[TradinGo] UPDATE_OPEN but no positions — opening fresh");
      return OpenSplitTrades(symbol, direction, lot, sl, tps, magicBase,
                             channelFile, json, entryStatus, rangeLo, rangeHi, distPoints);
     }

   // Fill/modify by magic index: never close+reopen (preserves re-entry tickets).
   // Planned slots 1..N: modify SL/TP if present, else open missing (UPDATE_FILL).
   // Extra tickets beyond N (allow_stack re-entries): new SL only, TP unchanged.
   string channelTag = ChannelShortTag(channelFile, json);
   int n = trades;
   if(n <= 0)
      n = MathMax(1, ArraySize(tps));
   if(n > 5)
      n = 5;

   bool any = false;
   for(int i = 1; i <= n; i++)
     {
      ulong magic = TradeMagic(magicBase, i);
      ulong ticket = FindOurPositionByMagic(symbol, magic);
      double tp = (i - 1 < ArraySize(tps)) ? tps[i - 1] : 0.0;
      if(ticket > 0)
        {
         if(ModifyPositionSLTP(ticket, sl, tp))
            any = true;
        }
      else
        {
         if(IsOpenBlocked(symbol))
            continue;
         Print("[TradinGo] UPDATE_OPEN fill missing magic=", magic,
               " slot=", i, "/", n);
         if(OpenMarket(symbol, direction, lot, sl, tp, magic,
                       BuildTradeComment(channelTag, i, json),
                       channelFile, json, "EXECUTED_UPDATE_FILL", 0, 0, 0, i))
            any = true;
        }
     }

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(g_pos.Symbol() != symbol)
         continue;
      if(!IsOurPosition(g_pos.Ticket(), magicBase, 8))
         continue;
      ulong mg = (ulong)g_pos.Magic();
      bool planned = false;
      for(int k = 1; k <= n; k++)
        {
         if(mg == TradeMagic(magicBase, k))
           {
            planned = true;
            break;
           }
        }
      if(planned)
         continue;
      // Re-entry / extra: apply SL only, keep existing TP (tp=0 → curTp).
      ModifyPositionSLTP(g_pos.Ticket(), sl, 0);
     }
   return true;
  }

//+------------------------------------------------------------------+
bool HandleUpdateTp(const string json)
  {
   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   int magicBase = JsonGetInt(json, "magic_base");
   double newTp = JsonGetNumber(json, "new_tp");
   if(newTp <= 0)
     {
      double tps[];
      JsonGetNumberArray(json, "tp_levels", tps);
      if(ArraySize(tps) > 0)
         newTp = tps[0];
     }
   // "Spostiamo TP 4 a 4376": il livello vale solo per lo split di quel TP.
   // Senza tp_index il TP vale per tutte le posizioni del canale (ORO, FOREX).
   int tpIndex = JsonGetInt(json, "tp_index");
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(g_pos.Symbol() != symbol)
         continue;
      if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
         continue;
      if(tpIndex > 0 && (ulong)g_pos.Magic() != TradeMagic(magicBase, tpIndex))
         continue;
      double useSl = 0.0;
      double useTp = newTp;
      if(InpProtectExistingLevels)
         FilterAdverseLevels(g_pos.Ticket(), useSl, useTp);
      if(useTp <= 0.0)
         continue;
      ulong ticket = g_pos.Ticket();
      if(ModifyPositionSLTP(ticket, 0, useTp))
         Print("[TradinGo] UPDATE_TP ticket=", ticket,
               " tp=", DoubleToString(useTp, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
               " tp_index=", (tpIndex > 0 ? IntegerToString(tpIndex) : "all"));
     }
   return true;
  }

//+------------------------------------------------------------------+
// Caps an SL that widens the risk at InpMaxSlWidenFactor times the current
// open->SL distance of the selected position. Returns the SL to apply.
double CapWidenedSl(const string symbol, const double newSl)
  {
   double curSl = g_pos.StopLoss();
   if(!InpProtectExistingLevels || InpMaxSlWidenFactor <= 0.0 ||
      curSl <= 0.0 || newSl <= 0.0)
      return newSl;
   double open = g_pos.PriceOpen();
   bool isBuy = (g_pos.PositionType() == POSITION_TYPE_BUY);
   if(isBuy ? (newSl >= curSl) : (newSl <= curSl))
      return newSl;
   double risk = MathAbs(open - curSl);
   if(risk <= 0.0)
      return newSl;
   double maxRisk = risk * InpMaxSlWidenFactor;
   if(MathAbs(open - newSl) <= maxRisk)
      return newSl;
   int dg = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double capped = NormalizeDouble(isBuy ? (open - maxRisk) : (open + maxRisk), dg);
   Print("[TradinGo] UPDATE_SL_WIDEN_CAPPED ticket=", g_pos.Ticket(),
         " new_sl=", DoubleToString(newSl, dg),
         " cur_sl=", DoubleToString(curSl, dg),
         " applied=", DoubleToString(capped, dg),
         " factor=", DoubleToString(InpMaxSlWidenFactor, 2),
         " side=", (isBuy ? "BUY" : "SELL"));
   return capped;
  }

//+------------------------------------------------------------------+
bool HandleUpdateSl(const string json)
  {
   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   int magicBase = JsonGetInt(json, "magic_base");
   double newSl = JsonGetNumber(json, "new_sl");
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(g_pos.Symbol() != symbol)
         continue;
      if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
         continue;
      // Lo stop che si allontana viene seguito, ma non oltre il tetto: la size
      // e' stata calcolata sul rischio precedente ("SL 4660" su un SELL con SL
      // 4656 e' gestione, un salto a 4800 moltiplicherebbe la perdita massima).
      double useSl = CapWidenedSl(symbol, newSl);
      ModifyPositionSLTP(g_pos.Ticket(), useSl, 0);
     }
   return true;
  }

//+------------------------------------------------------------------+
bool HandleCheckAndClose(const string json)
  {
   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   string direction = JsonGetString(json, "direction");
   int magicBase = JsonGetInt(json, "magic_base");
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(g_pos.Symbol() != symbol)
         continue;
      if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
         continue;
      string pdir = (g_pos.PositionType() == POSITION_TYPE_BUY) ? "BUY" : "SELL";
      if(pdir == direction)
         ClosePositionKeepMagic(g_pos.Ticket());
     }
   return true;
  }

//+------------------------------------------------------------------+
bool HandleCloseAllSymbol(const string json)
  {
   string symRaw = JsonGetString(json, "symbol");
   int magicBase = JsonGetInt(json, "magic_base");
   if(symRaw == "")
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         if(!g_pos.SelectByIndex(i))
            continue;
         if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
            continue;
         ClosePositionKeepMagic(g_pos.Ticket());
        }
      return true;
     }
   string symbol = ResolveSymbol(symRaw);
   CloseOurPositions(symbol, magicBase, 5);
   return true;
  }

//+------------------------------------------------------------------+
//| CLOSE_SELECTIVE keep=ALL_BUT_NEWEST: chiude solo l'ultimo blocco  |
//| aperto ("Chiudo la rientry"), lasciando il setup principale.      |
//| Blocco = posizioni aperte entro InpBatchWindowSec dalla più       |
//| recente. Se tutte le posizioni sono nello stesso blocco non c'è   |
//| nessun rientro da chiudere e il comando non fa nulla.             |
//+------------------------------------------------------------------+
bool CloseNewestBatch(const string json)
  {
   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   int magicBase = JsonGetInt(json, "magic_base");

   datetime newest = 0;
   datetime oldest = 0;
   int total = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(g_pos.Symbol() != symbol)
         continue;
      if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
         continue;
      datetime t = (datetime)g_pos.Time();
      if(total == 0 || t > newest)
         newest = t;
      if(total == 0 || t < oldest)
         oldest = t;
      total++;
     }
   if(total == 0)
     {
      Print("[TradinGo] CLOSE_SELECTIVE keep=ALL_BUT_NEWEST ", symbol,
            " — nessuna posizione nostra aperta");
      return true;
     }

   int window = (InpBatchWindowSec > 0 ? InpBatchWindowSec : 120);
   if((int)(newest - oldest) <= window)
     {
      Print("[TradinGo] CLOSE_SELECTIVE keep=ALL_BUT_NEWEST ", symbol,
            " ignorata — un solo blocco aperto (", total, " posizioni)");
      return true;
     }

   int closed = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(g_pos.Symbol() != symbol)
         continue;
      if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
         continue;
      if((int)(newest - (datetime)g_pos.Time()) > window)
         continue;
      ulong ticket = g_pos.Ticket();
      SetTrackReason(ticket, "CLOSE_SELECTIVE_ALL_BUT_NEWEST");
      if(ClosePositionKeepMagic(ticket))
         closed++;
      else
         Print("[TradinGo] CLOSE_SELECTIVE close failed ticket=", ticket,
               " err=", GetLastError());
     }
   Print("[TradinGo] CLOSE_SELECTIVE ", symbol,
         " keep=ALL_BUT_NEWEST newest=", TimeToString(newest, TIME_DATE | TIME_SECONDS),
         " closed=", closed, "/", total);
   return true;
  }

//+------------------------------------------------------------------+
//| CLOSE_SELECTIVE: chiude solo una parte delle entry.               |
//| keep=BEST     tiene le entry migliori per la direzione            |
//|               (SELL: prezzo più alto, BUY: prezzo più basso)      |
//| keep=HIGHEST  tiene le entry con prezzo di apertura più alto      |
//| keep=LOWEST   tiene le entry con prezzo di apertura più basso     |
//| Le posizioni allo stesso prezzo del gruppo tenuto restano aperte. |
//+------------------------------------------------------------------+
bool HandleCloseSelective(const string json)
  {
   string keep = JsonGetString(json, "keep");
   StringToUpper(keep);
   if(keep == "ALL_BUT_NEWEST")
      return CloseNewestBatch(json);
   if(keep != "BEST" && keep != "HIGHEST" && keep != "LOWEST")
     {
      Print("[TradinGo] CLOSE_SELECTIVE ignorata — keep non valido: '", keep, "'");
      return false;
     }

   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   int magicBase = JsonGetInt(json, "magic_base");

   // Il gruppo da tenere si determina per direzione: un SELL e un BUY sullo
   // stesso simbolo non sono confrontabili.
   for(int pass = 0; pass < 2; pass++)
     {
      ENUM_POSITION_TYPE side = (pass == 0 ? POSITION_TYPE_BUY : POSITION_TYPE_SELL);

      double keepPrice = 0.0;
      bool found = false;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         if(!g_pos.SelectByIndex(i))
            continue;
         if(g_pos.Symbol() != symbol || g_pos.PositionType() != side)
            continue;
         if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
            continue;
         double price = g_pos.PriceOpen();
         bool keepHigher = (keep == "HIGHEST")
                           || (keep == "BEST" && side == POSITION_TYPE_SELL);
         if(!found || (keepHigher ? price > keepPrice : price < keepPrice))
           {
            keepPrice = price;
            found = true;
           }
        }
      if(!found)
         continue;

      double tol = SymbolInfoDouble(symbol, SYMBOL_POINT) * 10.0;
      int closed = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         if(!g_pos.SelectByIndex(i))
            continue;
         if(g_pos.Symbol() != symbol || g_pos.PositionType() != side)
            continue;
         if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
            continue;
         if(MathAbs(g_pos.PriceOpen() - keepPrice) <= tol)
            continue;
         ulong ticket = g_pos.Ticket();
         SetTrackReason(ticket, "CLOSE_SELECTIVE_" + keep);
         if(ClosePositionKeepMagic(ticket))
            closed++;
         else
            Print("[TradinGo] CLOSE_SELECTIVE close failed ticket=", ticket,
                  " err=", GetLastError());
        }
      Print("[TradinGo] CLOSE_SELECTIVE ", symbol,
            " side=", (side == POSITION_TYPE_BUY ? "BUY" : "SELL"),
            " keep=", keep,
            " keep_price=", DoubleToString(keepPrice, _Digits),
            " closed=", closed);
     }
   return true;
  }

//+------------------------------------------------------------------+
bool HandleBreakEvenPrice(const string json)
  {
   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   int magicBase = JsonGetInt(json, "magic_base");
   double be = JsonGetNumber(json, "be_price");
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(g_pos.Symbol() != symbol)
         continue;
      if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
         continue;
      ModifyPositionSLTP(g_pos.Ticket(), be, 0);
     }
   return true;
  }

//+------------------------------------------------------------------+
bool HandleCloseHalfBe(const string json)
  {
   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   int magicBase = JsonGetInt(json, "magic_base");
   Print("[TradinGo] CLOSE_HALF_BE ", symbol, " — close 50% + SL to entry (clamped if needed)");
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(g_pos.Symbol() != symbol)
         continue;
      if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
         continue;
      ulong ticket = g_pos.Ticket();
      double vol = g_pos.Volume();
      double half = NormalizeLot(symbol, vol / 2.0);
      if(half >= SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN))
        {
         if(!ClosePartialKeepMagic(ticket, half))
            Print("[TradinGo] CLOSE_HALF partial failed ticket=", ticket,
                  " err=", g_trade.ResultRetcode());
         else
            Print("[TradinGo] CLOSE_HALF ok ticket=", ticket,
                  " closed=", DoubleToString(half, 2));
        }
      // Re-select: after partial the ticket remains for the leftover volume.
      ApplyBreakEvenSL(ticket);
     }
   return true;
  }

//+------------------------------------------------------------------+
bool HandleCheckAndBe(const string json)
  {
   int magicBase = JsonGetInt(json, "magic_base");
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(!IsOurPosition(g_pos.Ticket(), magicBase, 5))
         continue;
      ApplyBreakEvenSL(g_pos.Ticket());
     }
   return true;
  }

//+------------------------------------------------------------------+
bool HandleCheckAndCloseTp(const string json)
  {
   int magicBase = JsonGetInt(json, "magic_base");
   int tpIndex = JsonGetInt(json, "tp_index");
   if(tpIndex <= 0)
      tpIndex = 1;
   ulong targetMagic = TradeMagic(magicBase, tpIndex);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if((ulong)g_pos.Magic() != targetMagic)
         continue;
      ClosePositionKeepMagic(g_pos.Ticket());
     }
   return true;
  }

//+------------------------------------------------------------------+
bool ProcessSignalJson(const string channelFile, const string json)
  {
   string action = JsonGetString(json, "action");
   if(action == "" || action == "NONE")
      return false;

   string ts = JsonGetString(json, "timestamp");
   for(int c = 0; c < g_channelCount; c++)
     {
      if(g_channelFile[c] == channelFile)
        {
         if(ts != "" && ts == g_lastTimestamp[c])
            return false;
         if(ts != "")
            g_lastTimestamp[c] = ts;
         break;
        }
     }

   Print("[TradinGo] ", channelFile, " action=", action,
         " sym=", JsonGetString(json, "symbol"),
         " ts=", ts);

   bool handled = false;
   if(action == "OPEN" || action == "OPEN_NOW")
      handled = HandleOpen(channelFile, json);
   else if(action == "UPDATE_OPEN")
      handled = HandleUpdateOpen(channelFile, json);
   else if(action == "UPDATE_TP")
      handled = HandleUpdateTp(json);
   else if(action == "UPDATE_SL")
      handled = HandleUpdateSl(json);
   else if(action == "CHECK_AND_CLOSE")
      handled = HandleCheckAndClose(json);
   else if(action == "CLOSE_ALL_SYMBOL")
      handled = HandleCloseAllSymbol(json);
   else if(action == "CLOSE_SELECTIVE")
      handled = HandleCloseSelective(json);
   else if(action == "BREAK_EVEN_PRICE")
      handled = HandleBreakEvenPrice(json);
   else if(action == "CLOSE_HALF_BE")
      handled = HandleCloseHalfBe(json);
   else if(action == "CHECK_AND_BE")
      handled = HandleCheckAndBe(json);
   else if(action == "CHECK_AND_CLOSE_TP")
      handled = HandleCheckAndCloseTp(json);
   else
      Print("[TradinGo] Unknown action: ", action);

   // Clear after any recognized action attempt (including cancelled range / failed open)
   // so a restart cannot replay the same JSON.
   if(InpClearSignalAfterProcess && action != "" && action != "NONE")
      ClearSignalFile(channelFile);
   return handled;
  }

//+------------------------------------------------------------------+
void PollChannel(const int index)
  {
   string json;
   if(!ReadSignalFile(g_channelFile[index], json))
      return;
   ProcessSignalJson(g_channelFile[index], json);
  }

//+------------------------------------------------------------------+
void ParseChannels()
  {
   g_channelCount = 0;
   string parts[];
   int n = StringSplit(InpChannels, ',', parts);
   for(int i = 0; i < n && g_channelCount < MAX_CHANNELS; i++)
     {
      string suf = Trim(parts[i]);
      if(suf == "")
         continue;
      g_channelSuffix[g_channelCount] = suf;
      g_channelFile[g_channelCount] = "signal_ch_" + suf + ".json";
      g_lastTimestamp[g_channelCount] = "";
      g_channelCount++;
     }
  }

//+------------------------------------------------------------------+
void SeedAndClearExistingSignals()
  {
   // Critical: on attach/recompile, do NOT execute leftover JSON from hours/days ago.
   for(int i = 0; i < g_channelCount; i++)
     {
      string json;
      if(!ReadSignalFile(g_channelFile[i], json))
         continue;
      string action = JsonGetString(json, "action");
      string ts = JsonGetString(json, "timestamp");
      if(ts != "")
         g_lastTimestamp[i] = ts;
      if(action == "" || action == "NONE")
         continue;
      Print("[TradinGo] Init skip stale ", g_channelFile[i],
            " action=", action, " ts=", ts, " -> NONE (no exec)");
      ClearSignalFile(g_channelFile[i]);
     }
  }

//+==================================================================+
//| v2.10 hardening / journaling                                     |
//+==================================================================+
#define TG_LOCK_NAME "TG_TRADINGO_EA_LOCK"
#define TG_JOURNAL_HEADER "event,ts_utc,signal_id,channel,tp_index,ticket,magic,symbol,direction,req_entry,range_lo,range_hi,req_sl,req_tp,volume,fill,slippage_pts,open_time_utc,close_time_utc,close_price,close_reason,realized_profit,mae_price,mfe_price,mae_ccy,mfe_ccy,balance,equity,margin\n"

//--- killswitch cooldown per bucket
string   g_ksBucket[];
datetime g_ksUntil[];

//--- bridge heartbeat state
bool     g_bridgeStale = false;

//--- v2.12 equity-drawdown guard / manual kill switch / reservoir sizing
#define TG_DD_START_NAME "TG_TRADINGO_DD_START"
#define TG_DD_PEAK_NAME  "TG_TRADINGO_DD_PEAK"
#define TG_DD_HALT_NAME  "TG_TRADINGO_DD_HALT"
double   g_ddStart      = 0.0;  // static reference equity (never recomputed)
double   g_ddPeakClosed = 0.0;  // peak of closed equity (balance), never decreases
bool     g_ddHalted     = false;
bool     g_ddRecoveryOn = false;
bool     g_haltFlag     = false;
datetime g_lastHaltCheck = 0;
string   g_ddLotKey[];
double   g_ddLotVal[];

//--- equity sampling
datetime g_lastEquitySample = 0;

//--- market context: signal_ids already written
string   g_ctxDone[];

//--- open-trade tracking (parallel arrays)
ulong    g_trkTicket[];
string   g_trkSignalId[];
string   g_trkChannel[];
int      g_trkTpIndex[];
ulong    g_trkMagic[];
string   g_trkSymbol[];
string   g_trkDir[];
double   g_trkReqEntry[];
double   g_trkRangeLo[];
double   g_trkRangeHi[];
double   g_trkReqSl[];
double   g_trkReqTp[];
double   g_trkVolume[];
double   g_trkFill[];
double   g_trkSlippage[];
datetime g_trkOpenTime[];
double   g_trkMinPrice[];
double   g_trkMaxPrice[];
double   g_trkMaeCcy[];
double   g_trkMfeCcy[];
string   g_trkCloseReason[];

//+------------------------------------------------------------------+
string IsoUtc(const datetime t)
  {
   MqlDateTime dt;
   TimeToStruct(t, dt);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                       dt.year, dt.mon, dt.day, dt.hour, dt.min, dt.sec);
  }

//+------------------------------------------------------------------+
string YmdUtc(const datetime t)
  {
   MqlDateTime dt;
   TimeToStruct(t, dt);
   return StringFormat("%04d%02d%02d", dt.year, dt.mon, dt.day);
  }

//+------------------------------------------------------------------+
datetime ParseIso8601(const string s)
  {
   // Expect YYYY-MM-DDTHH:MM:SS (T or space separator); ignore trailing fraction/zone.
   if(StringLen(s) < 19)
      return 0;
   MqlDateTime dt;
   dt.year = (int)StringToInteger(StringSubstr(s, 0, 4));
   dt.mon  = (int)StringToInteger(StringSubstr(s, 5, 2));
   dt.day  = (int)StringToInteger(StringSubstr(s, 8, 2));
   dt.hour = (int)StringToInteger(StringSubstr(s, 11, 2));
   dt.min  = (int)StringToInteger(StringSubstr(s, 14, 2));
   dt.sec  = (int)StringToInteger(StringSubstr(s, 17, 2));
   if(dt.year < 1970 || dt.mon < 1 || dt.day < 1)
      return 0;
   return StructToTime(dt);
  }

//+------------------------------------------------------------------+
string SymbolBucket(const string symbol)
  {
   string up = symbol;
   StringToUpper(up);
   if(StringFind(up, "XAU") >= 0 || StringFind(up, "XAG") >= 0)
      return "METALS";
   // base name without configured suffix
   string base = symbol;
   if(StringLen(InpSymbolSuffix) > 0)
     {
      int p = StringFind(base, InpSymbolSuffix);
      if(p >= 0)
         base = StringSubstr(base, 0, p);
     }
   StringToUpper(base);
   return base;
  }

//+------------------------------------------------------------------+
bool CommentStartsWithTag(const string comment, const string tag)
  {
   if(tag == "" || comment == "")
      return false;
   string prefix = tag + "-";
   return (StringFind(comment, prefix) == 0);
  }

//+------------------------------------------------------------------+
bool IsTGPositionSelected()
  {
   // Positions from this EA: legacy TG-* or compact IT-/AS-/custom tags.
   string c = g_pos.Comment();
   if(StringFind(c, "TG-") == 0)
      return true;
   if(CommentStartsWithTag(c, InpTagIvan))
      return true;
   if(CommentStartsWithTag(c, InpTagStark))
      return true;
   if(CommentStartsWithTag(c, InpTagGold))
      return true;
   if(CommentStartsWithTag(c, InpTagOro))
      return true;
   if(CommentStartsWithTag(c, InpTagForex))
      return true;
   return false;
  }

//+------------------------------------------------------------------+
string JournalPath(const string sub)
  {
   string b = InpJournalPrefix;
   if(StringLen(b) > 0)
     {
      ushort last = StringGetCharacter(b, StringLen(b) - 1);
      if(last != '\\' && last != '/')
         b += "\\";
     }
   return b + sub;
  }

//+------------------------------------------------------------------+
string SanitizeName(const string s)
  {
   string o = "";
   for(int i = 0; i < StringLen(s); i++)
     {
      ushort c = StringGetCharacter(s, i);
      bool ok = (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') ||
                (c >= 'a' && c <= 'z') || c == '_' || c == '-';
      o += ok ? ShortToString(c) : "_";
     }
   if(o == "")
      o = "unknown";
   return o;
  }

//+------------------------------------------------------------------+
string JoinCsv(const string &f[])
  {
   string o = "";
   for(int k = 0; k < ArraySize(f); k++)
     {
      if(k > 0)
         o += ",";
      o += f[k];
     }
   return o + "\n";
  }

//+------------------------------------------------------------------+
void AppendCsvLine(const string path, const string header, const string line)
  {
   // FileOpen auto-creates sub-directories under MQL5\\Files.
   bool isNew = false;
   ResetLastError();
   int h = FileOpen(path, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
     {
      ResetLastError();
      h = FileOpen(path, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
      isNew = true;
     }
   if(h == INVALID_HANDLE)
     {
      Print("[TradinGo] journal open failed path=", path, " err=", GetLastError());
      return;
     }
   if(isNew || FileSize(h) == 0)
      FileWriteString(h, header);
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, line);
   FileClose(h);
  }

//+------------------------------------------------------------------+
bool ReadJsonFileQuiet(const string fileName, string &content)
  {
   // Same candidate paths as ReadSignalFile, but silent (used for heartbeat polling).
   string candidates[];
   int n = 0;
   if(InpUseAbsolutePath && StringLen(InpSignalsPath) > 0)
     {
      ArrayResize(candidates, n + 1);
      candidates[n++] = BuildAbsoluteSignalPath(fileName);
     }
   ArrayResize(candidates, n + 1);
   candidates[n++] = BuildRelativeSignalPath(fileName);
   ArrayResize(candidates, n + 1);
   candidates[n++] = fileName;
   for(int i = 0; i < n; i++)
      if(ReadTextFileContent(candidates[i], content))
         return true;
   return false;
  }

//+------------------------------------------------------------------+
//| Killswitch cooldown helpers                                      |
//+------------------------------------------------------------------+
datetime KsCooldownGet(const string bucket)
  {
   for(int i = 0; i < ArraySize(g_ksBucket); i++)
      if(g_ksBucket[i] == bucket)
         return g_ksUntil[i];
   return 0;
  }

//+------------------------------------------------------------------+
bool KsCooldownActive(const string bucket)
  {
   datetime u = KsCooldownGet(bucket);
   return (u > 0 && TimeCurrent() < u);
  }

//+------------------------------------------------------------------+
void KsSetCooldown(const string bucket)
  {
   // 0 = nessun blocco: dopo la chiusura del bucket i segnali successivi
   // restano eseguibili (il floor guard sull'equity resta la protezione).
   if(InpKillSwitchCooldownMin <= 0)
      return;
   datetime until = TimeCurrent() + (datetime)InpKillSwitchCooldownMin * 60;
   for(int i = 0; i < ArraySize(g_ksBucket); i++)
      if(g_ksBucket[i] == bucket)
        {
         g_ksUntil[i] = until;
         return;
        }
   int n = ArraySize(g_ksBucket);
   ArrayResize(g_ksBucket, n + 1);
   ArrayResize(g_ksUntil, n + 1);
   g_ksBucket[n] = bucket;
   g_ksUntil[n] = until;
  }

//+==================================================================+
//| v2.12 equity drawdown guard (static floor)                       |
//+==================================================================+
bool DdGuardEnabled()
  {
   return (InpDdMaxPct > 0.0 && g_ddStart > 0.0);
  }

//+------------------------------------------------------------------+
double DdFloor()
  {
   return g_ddStart * (1.0 - InpDdMaxPct / 100.0);
  }

//+------------------------------------------------------------------+
double DdAllowance()
  {
   return g_ddStart - DdFloor();
  }

//+------------------------------------------------------------------+
//| Equity level at which a given share of the allowance is consumed |
//+------------------------------------------------------------------+
double DdLevelForConsumedPct(const double consumedPct)
  {
   double pct = MathMax(0.0, MathMin(100.0, consumedPct));
   return DdFloor() + DdAllowance() * (1.0 - pct / 100.0);
  }

//+------------------------------------------------------------------+
//| Reservoir = peak closed equity - floor. Grows with realized      |
//| profits only: floating gains never inflate it.                   |
//+------------------------------------------------------------------+
double DdReservoir()
  {
   if(!DdGuardEnabled())
      return 0.0;
   return MathMax(0.0, g_ddPeakClosed - DdFloor());
  }

//+------------------------------------------------------------------+
//| Between the block level and the close-all level the account is    |
//| not dead: opens continue at InpDdRecoveryLot instead of stopping. |
//+------------------------------------------------------------------+
bool DdRecoveryActive()
  {
   if(InpDdRecoveryLot <= 0.0 || !DdGuardEnabled() || g_ddHalted)
      return false;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   return (equity <= DdLevelForConsumedPct(InpDdBlockNewAtPct));
  }

//+------------------------------------------------------------------+
void DdLogRecoveryState(const bool active, const double equity)
  {
   if(active == g_ddRecoveryOn)
      return;
   g_ddRecoveryOn = active;
   if(active)
      Print("[TradinGo] DD_RECOVERY ON — equity=", DoubleToString(equity, 2),
            " <= block_level=", DoubleToString(DdLevelForConsumedPct(InpDdBlockNewAtPct), 2),
            ": opens continue at lot=", DoubleToString(InpDdRecoveryLot, 2),
            " until equity recovers (close_all still at ",
            DoubleToString(DdLevelForConsumedPct(InpDdCloseAtPct), 2), ")");
   else
      Print("[TradinGo] DD_RECOVERY OFF — equity=", DoubleToString(equity, 2),
            " back above block_level=",
            DoubleToString(DdLevelForConsumedPct(InpDdBlockNewAtPct), 2),
            ": normal sizing restored");
  }

//+------------------------------------------------------------------+
void DdSetHalted(const bool halted)
  {
   g_ddHalted = halted;
   GlobalVariableSet(TG_DD_HALT_NAME, halted ? 1.0 : 0.0);
  }

//+------------------------------------------------------------------+
void DdTrackPeakClosed()
  {
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(bal > g_ddPeakClosed)
     {
      g_ddPeakClosed = bal;
      GlobalVariableSet(TG_DD_PEAK_NAME, g_ddPeakClosed);
     }
  }

//+------------------------------------------------------------------+
void DdInit()
  {
   if(InpDdMaxPct <= 0.0)
     {
      Print("[TradinGo] DD_GUARD disabled (InpDdMaxPct=0) — demo/neutral mode");
      return;
     }
   if(InpDdStartEquity > 0.0)
      g_ddStart = InpDdStartEquity;
   else if(GlobalVariableCheck(TG_DD_START_NAME))
      g_ddStart = GlobalVariableGet(TG_DD_START_NAME);
   else
      g_ddStart = AccountInfoDouble(ACCOUNT_EQUITY);
   GlobalVariableSet(TG_DD_START_NAME, g_ddStart);

   g_ddPeakClosed = GlobalVariableCheck(TG_DD_PEAK_NAME)
                    ? GlobalVariableGet(TG_DD_PEAK_NAME) : 0.0;
   g_ddPeakClosed = MathMax(g_ddPeakClosed, g_ddStart);
   DdTrackPeakClosed();

   g_ddHalted = (GlobalVariableCheck(TG_DD_HALT_NAME)
                 && GlobalVariableGet(TG_DD_HALT_NAME) > 0.5);

   Print("[TradinGo] DD_GUARD armed | start=", DoubleToString(g_ddStart, 2),
         " max_dd_pct=", DoubleToString(InpDdMaxPct, 2),
         " floor=", DoubleToString(DdFloor(), 2),
         " allowance=", DoubleToString(DdAllowance(), 2),
         " close_all_at_equity=", DoubleToString(DdLevelForConsumedPct(InpDdCloseAtPct), 2),
         " block_new_at_equity=", DoubleToString(DdLevelForConsumedPct(InpDdBlockNewAtPct), 2),
         " peak_closed=", DoubleToString(g_ddPeakClosed, 2),
         " reservoir=", DoubleToString(DdReservoir(), 2),
         " halted=", (g_ddHalted ? "YES (manual reset required)" : "no"),
         " ccy=", AccountInfoString(ACCOUNT_CURRENCY));
   if(g_ddHalted)
      Print("[TradinGo] CRITICAL DD_HALT still active from a previous breach. ",
            "To resume: delete GlobalVariable '", TG_DD_HALT_NAME, "'.");
  }

//+------------------------------------------------------------------+
void CloseAllOurPositions(const string reason)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(!IsTGPositionSelected())
         continue;
      ulong tk = g_pos.Ticket();
      SetTrackReason(tk, reason);
      if(!ClosePositionKeepMagic(tk))
         Print("[TradinGo] ERROR close failed ticket=", tk,
               " reason=", reason, " ret=", g_trade.ResultRetcode(),
               " (", g_trade.ResultRetcodeDescription(), ")");
     }
  }

//+------------------------------------------------------------------+
//| Closes everything and latches the halt well BEFORE the floor:    |
//| waiting for the exact floor means breaching it, fills come late.  |
//+------------------------------------------------------------------+
void CheckEquityFloorGuard()
  {
   if(!DdGuardEnabled())
      return;
   DdTrackPeakClosed();
   if(g_ddHalted)
      return;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double trip = DdLevelForConsumedPct(InpDdCloseAtPct);
   if(equity > trip)
      return;
   Print("[TradinGo] CRITICAL DD_BREACH equity=", DoubleToString(equity, 2),
         " <= trip=", DoubleToString(trip, 2),
         " floor=", DoubleToString(DdFloor(), 2),
         " consumed_pct_of_allowance=",
         DoubleToString(100.0 * (g_ddStart - equity) / MathMax(0.01, DdAllowance()), 1),
         " -> closing ALL TG positions, halting new opens");
   CloseAllOurPositions("KILLSWITCH_EQUITY_FLOOR");
   DdSetHalted(true);
  }

//+==================================================================+
//| v2.12 manual kill switch (flag file, no restart needed)          |
//+==================================================================+
bool HaltFlagExists()
  {
   if(StringLen(InpHaltFlagFile) == 0)
      return false;
   if(FileIsExist(InpHaltFlagFile))
      return true;
   if(StringLen(InpSignalsPath) > 0 && !InpUseAbsolutePath
      && FileIsExist(BuildRelativeSignalPath(InpHaltFlagFile)))
      return true;
   return false;
  }

//+------------------------------------------------------------------+
void CheckHaltFlag()
  {
   if(StringLen(InpHaltFlagFile) == 0)
      return;
   datetime now = TimeCurrent();
   if(g_lastHaltCheck != 0 && (now - g_lastHaltCheck) < 2)
      return;
   g_lastHaltCheck = now;
   bool present = HaltFlagExists();
   if(present == g_haltFlag)
      return;
   g_haltFlag = present;
   if(present)
      Print("[TradinGo] KILL_SWITCH ON — flag file '", InpHaltFlagFile,
            "' present: new opens blocked, open positions keep normal management");
   else
      Print("[TradinGo] KILL_SWITCH OFF — flag file '", InpHaltFlagFile,
            "' removed: opens allowed again");
  }

//+==================================================================+
//| v2.12 concurrent exposure cap                                    |
//+==================================================================+
double TotalOurLots()
  {
   double lots = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(!IsTGPositionSelected())
         continue;
      lots += g_pos.Volume();
     }
   return lots;
  }

//+------------------------------------------------------------------+
//| Evaluated per position, so the first TPs of a signal still open  |
//| and only the volume above the cap is rejected.                   |
//+------------------------------------------------------------------+
bool ExposureAllows(const double lot)
  {
   if(InpMaxConcurrentLots <= 0.0)
      return true;
   double open = TotalOurLots();
   if(open + lot <= InpMaxConcurrentLots + 1e-8)
      return true;
   Print("[TradinGo] OPEN rejected — concurrent lots cap: open=",
         DoubleToString(open, 2), " + ", DoubleToString(lot, 2),
         " > cap=", DoubleToString(InpMaxConcurrentLots, 2));
   return false;
  }

//+==================================================================+
//| v2.12 reservoir-based sizing                                     |
//+==================================================================+
double DdFloatPer001(const string channelKey)
  {
   if(channelKey == "ivan")
      return InpDdFloatIvan;
   if(channelKey == "stark")
      return InpDdFloatStark;
   if(channelKey == "gold")
      return InpDdFloatGold;
   if(channelKey == "oro")
      return InpDdFloatOro;
   if(channelKey == "forex")
      return InpDdFloatForex;
   return 0.0;
  }

//+------------------------------------------------------------------+
double DdLotCacheGet(const string key)
  {
   for(int i = 0; i < ArraySize(g_ddLotKey); i++)
      if(g_ddLotKey[i] == key)
         return g_ddLotVal[i];
   return 0.0;
  }

//+------------------------------------------------------------------+
void DdLotCacheSet(const string key, const double lot)
  {
   for(int i = 0; i < ArraySize(g_ddLotKey); i++)
      if(g_ddLotKey[i] == key)
        {
         g_ddLotVal[i] = lot;
         return;
        }
   int n = ArraySize(g_ddLotKey);
   ArrayResize(g_ddLotKey, n + 1);
   ArrayResize(g_ddLotVal, n + 1);
   g_ddLotKey[n] = key;
   g_ddLotVal[n] = lot;
  }

//+------------------------------------------------------------------+
//| lot = (reservoir * util%) / worst_floating_per_0.01_lot          |
//| Quantized in InpDdStepPct steps of the initial allowance, and    |
//| refreshed only while flat, so an open signal keeps its size.     |
//+------------------------------------------------------------------+
double DdSizedLot(const string symbol, const string channelKey, const double fallbackLot)
  {
   if(DdRecoveryActive())
      return MathMax(InpDdRecoveryLot, SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN));
   if(!InpDdSizingEnabled || !DdGuardEnabled())
      return fallbackLot;
   double per001 = DdFloatPer001(channelKey);
   if(per001 <= 0.0)
     {
      Print("[TradinGo] DD_SIZING skipped — no floating-per-0.01 reference for channel=",
            channelKey, " (using lot=", DoubleToString(fallbackLot, 2), ")");
      return fallbackLot;
     }
   double cached = DdLotCacheGet(channelKey);
   if(cached > 0.0 && TotalOurLots() > 0.0)
      return cached; // never resize while positions are open

   double reservoir = DdReservoir();
   if(InpDdStepPct > 0.0)
     {
      double unit = DdAllowance() * InpDdStepPct / 100.0;
      if(unit > 0.0)
         reservoir = MathFloor(reservoir / unit) * unit;
     }
   double lot = (reservoir * InpDdUtilizationPct / 100.0) / per001 * 0.01;
   if(InpDdMaxLot > 0.0)
      lot = MathMin(lot, InpDdMaxLot);
   lot = NormalizeLot(symbol, lot);
   double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   if(lot < vmin || lot <= 0.0)
     {
      Print("[TradinGo] DD_SIZING below broker minimum — channel=", channelKey,
            " reservoir=", DoubleToString(reservoir, 2),
            " computed=", DoubleToString(lot, 2),
            " min=", DoubleToString(vmin, 2), " -> opens blocked for this signal");
      DdLotCacheSet(channelKey, 0.0);
      return 0.0;
     }
   if(MathAbs(lot - cached) > 1e-8)
      Print("[TradinGo] DD_SIZING channel=", channelKey,
            " reservoir=", DoubleToString(reservoir, 2),
            " util=", DoubleToString(InpDdUtilizationPct, 1), "%",
            " float_per_0.01=", DoubleToString(per001, 2),
            " lot=", DoubleToString(lot, 2),
            " (was ", DoubleToString(cached, 2), ")");
   DdLotCacheSet(channelKey, lot);
   return lot;
  }

//+------------------------------------------------------------------+
bool IsOpenBlocked(const string symbol)
  {
   if(g_ddHalted)
     {
      Print("[TradinGo] OPEN blocked — DD_HALT latched (equity floor guard). ",
            "Manual reset required: delete GlobalVariable '", TG_DD_HALT_NAME, "'.");
      return true;
     }
   if(g_haltFlag)
     {
      Print("[TradinGo] OPEN blocked — kill switch flag '", InpHaltFlagFile, "' present");
      return true;
     }
   if(DdGuardEnabled())
     {
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      double blockLevel = DdLevelForConsumedPct(InpDdBlockNewAtPct);
      DdLogRecoveryState(DdRecoveryActive(), equity);
      if(equity <= blockLevel && !DdRecoveryActive())
        {
         Print("[TradinGo] OPEN blocked — equity=", DoubleToString(equity, 2),
               " <= block_level=", DoubleToString(blockLevel, 2),
               " (", DoubleToString(InpDdBlockNewAtPct, 0), "% of DD allowance consumed)");
         return true;
        }
     }
   if(g_bridgeStale)
     {
      Print("[TradinGo] OPEN blocked — bridge heartbeat STALE (opens suspended; updates/closes still allowed)");
      return true;
     }
   string b = SymbolBucket(symbol);
   if(KsCooldownActive(b))
     {
      Print("[TradinGo] OPEN blocked — killswitch cooldown active bucket=", b,
            " until=", TimeToString(KsCooldownGet(b), TIME_DATE | TIME_SECONDS));
      return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Trade tracking                                                   |
//+------------------------------------------------------------------+
int TrackFind(const ulong ticket)
  {
   for(int i = 0; i < ArraySize(g_trkTicket); i++)
      if(g_trkTicket[i] == ticket)
         return i;
   return -1;
  }

//+------------------------------------------------------------------+
void TrackRemoveAt(const int i)
  {
   int n = ArraySize(g_trkTicket);
   if(i < 0 || i >= n)
      return;
   int last = n - 1;
   g_trkTicket[i]      = g_trkTicket[last];
   g_trkSignalId[i]    = g_trkSignalId[last];
   g_trkChannel[i]     = g_trkChannel[last];
   g_trkTpIndex[i]     = g_trkTpIndex[last];
   g_trkMagic[i]       = g_trkMagic[last];
   g_trkSymbol[i]      = g_trkSymbol[last];
   g_trkDir[i]         = g_trkDir[last];
   g_trkReqEntry[i]    = g_trkReqEntry[last];
   g_trkRangeLo[i]     = g_trkRangeLo[last];
   g_trkRangeHi[i]     = g_trkRangeHi[last];
   g_trkReqSl[i]       = g_trkReqSl[last];
   g_trkReqTp[i]       = g_trkReqTp[last];
   g_trkVolume[i]      = g_trkVolume[last];
   g_trkFill[i]        = g_trkFill[last];
   g_trkSlippage[i]    = g_trkSlippage[last];
   g_trkOpenTime[i]    = g_trkOpenTime[last];
   g_trkMinPrice[i]    = g_trkMinPrice[last];
   g_trkMaxPrice[i]    = g_trkMaxPrice[last];
   g_trkMaeCcy[i]      = g_trkMaeCcy[last];
   g_trkMfeCcy[i]      = g_trkMfeCcy[last];
   g_trkCloseReason[i] = g_trkCloseReason[last];
   ArrayResize(g_trkTicket, last);
   ArrayResize(g_trkSignalId, last);
   ArrayResize(g_trkChannel, last);
   ArrayResize(g_trkTpIndex, last);
   ArrayResize(g_trkMagic, last);
   ArrayResize(g_trkSymbol, last);
   ArrayResize(g_trkDir, last);
   ArrayResize(g_trkReqEntry, last);
   ArrayResize(g_trkRangeLo, last);
   ArrayResize(g_trkRangeHi, last);
   ArrayResize(g_trkReqSl, last);
   ArrayResize(g_trkReqTp, last);
   ArrayResize(g_trkVolume, last);
   ArrayResize(g_trkFill, last);
   ArrayResize(g_trkSlippage, last);
   ArrayResize(g_trkOpenTime, last);
   ArrayResize(g_trkMinPrice, last);
   ArrayResize(g_trkMaxPrice, last);
   ArrayResize(g_trkMaeCcy, last);
   ArrayResize(g_trkMfeCcy, last);
   ArrayResize(g_trkCloseReason, last);
  }

//+------------------------------------------------------------------+
void SetTrackReason(const ulong ticket, const string reason)
  {
   int i = TrackFind(ticket);
   if(i >= 0)
      g_trkCloseReason[i] = reason;
  }

//+------------------------------------------------------------------+
int TrackAppendSlot()
  {
   int n = ArraySize(g_trkTicket);
   ArrayResize(g_trkTicket, n + 1);
   ArrayResize(g_trkSignalId, n + 1);
   ArrayResize(g_trkChannel, n + 1);
   ArrayResize(g_trkTpIndex, n + 1);
   ArrayResize(g_trkMagic, n + 1);
   ArrayResize(g_trkSymbol, n + 1);
   ArrayResize(g_trkDir, n + 1);
   ArrayResize(g_trkReqEntry, n + 1);
   ArrayResize(g_trkRangeLo, n + 1);
   ArrayResize(g_trkRangeHi, n + 1);
   ArrayResize(g_trkReqSl, n + 1);
   ArrayResize(g_trkReqTp, n + 1);
   ArrayResize(g_trkVolume, n + 1);
   ArrayResize(g_trkFill, n + 1);
   ArrayResize(g_trkSlippage, n + 1);
   ArrayResize(g_trkOpenTime, n + 1);
   ArrayResize(g_trkMinPrice, n + 1);
   ArrayResize(g_trkMaxPrice, n + 1);
   ArrayResize(g_trkMaeCcy, n + 1);
   ArrayResize(g_trkMfeCcy, n + 1);
   ArrayResize(g_trkCloseReason, n + 1);
   return n;
  }

//+------------------------------------------------------------------+
void WriteJournalOpen(const int i)
  {
   int dg = (int)SymbolInfoInteger(g_trkSymbol[i], SYMBOL_DIGITS);
   if(dg <= 0)
      dg = 5;
   string f[29];
   f[0]  = "OPEN";
   f[1]  = IsoUtc(TimeGMT());
   f[2]  = g_trkSignalId[i];
   f[3]  = g_trkChannel[i];
   f[4]  = IntegerToString(g_trkTpIndex[i]);
   f[5]  = IntegerToString((long)g_trkTicket[i]);
   f[6]  = IntegerToString((long)g_trkMagic[i]);
   f[7]  = g_trkSymbol[i];
   f[8]  = g_trkDir[i];
   f[9]  = DoubleToString(g_trkReqEntry[i], dg);
   f[10] = DoubleToString(g_trkRangeLo[i], dg);
   f[11] = DoubleToString(g_trkRangeHi[i], dg);
   f[12] = DoubleToString(g_trkReqSl[i], dg);
   f[13] = DoubleToString(g_trkReqTp[i], dg);
   f[14] = DoubleToString(g_trkVolume[i], 2);
   f[15] = DoubleToString(g_trkFill[i], dg);
   f[16] = DoubleToString(g_trkSlippage[i], 1);
   f[17] = IsoUtc(g_trkOpenTime[i]);
   f[18] = "";  // close_time_utc
   f[19] = "";  // close_price
   f[20] = "";  // close_reason
   f[21] = "";  // realized_profit
   f[22] = "";  // mae_price
   f[23] = "";  // mfe_price
   f[24] = "";  // mae_ccy
   f[25] = "";  // mfe_ccy
   f[26] = DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2);
   f[27] = DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2);
   f[28] = DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2);
   AppendCsvLine(JournalPath("trades\\trades_" + YmdUtc(TimeGMT()) + ".csv"),
                 TG_JOURNAL_HEADER, JoinCsv(f));
  }

//+------------------------------------------------------------------+
void TrackAddOpen(const ulong ticket, const string sid, const string channel,
                  const int tpIndex, const ulong magic, const string symbol,
                  const string dir, const double reqEntry,
                  const double rangeLo, const double rangeHi,
                  const double reqSl, const double reqTp, const double vol,
                  const double fill, const double slip, const datetime otime)
  {
   if(ticket == 0)
      return;
   int i = TrackFind(ticket);
   if(i < 0)
      i = TrackAppendSlot();
   g_trkTicket[i]      = ticket;
   g_trkSignalId[i]    = sid;
   g_trkChannel[i]     = channel;
   g_trkTpIndex[i]     = tpIndex;
   g_trkMagic[i]       = magic;
   g_trkSymbol[i]      = symbol;
   g_trkDir[i]         = dir;
   g_trkReqEntry[i]    = reqEntry;
   g_trkRangeLo[i]     = rangeLo;
   g_trkRangeHi[i]     = rangeHi;
   g_trkReqSl[i]       = reqSl;
   g_trkReqTp[i]       = reqTp;
   g_trkVolume[i]      = vol;
   g_trkFill[i]        = fill;
   g_trkSlippage[i]    = slip;
   g_trkOpenTime[i]    = otime;
   g_trkMinPrice[i]    = (fill > 0.0) ? fill : 0.0;
   g_trkMaxPrice[i]    = (fill > 0.0) ? fill : 0.0;
   g_trkMaeCcy[i]      = 0.0;
   g_trkMfeCcy[i]      = 0.0;
   g_trkCloseReason[i] = "";
   WriteJournalOpen(i);
  }

//+------------------------------------------------------------------+
void ParseComment(const string cm, string &tag, int &tpIdx, string &sid)
  {
   tag = "";
   tpIdx = 0;
   sid = "";
   string rest = cm;
   if(StringFind(rest, "TG-") == 0)
      rest = StringSubstr(rest, 3);
   // Compact tags already start with IT-/AS- — leave as-is for split.
   string parts[];
   int n = StringSplit(rest, '-', parts);
   if(n >= 1)
      tag = parts[0];
   int idx = 1;
   if(n >= 2)
     {
      string p1 = parts[1];
      if(StringLen(p1) >= 2 && StringGetCharacter(p1, 0) == 'T')
        {
         string num = StringSubstr(p1, 1);
         bool isnum = (StringLen(num) > 0);
         for(int c = 0; c < StringLen(num); c++)
           {
            ushort ch = StringGetCharacter(num, c);
            if(ch < '0' || ch > '9')
              {
               isnum = false;
               break;
              }
           }
         if(isnum)
           {
            tpIdx = (int)StringToInteger(num);
            idx = 2;
           }
        }
     }
   for(int j = idx; j < n; j++)
     {
      if(j > idx)
         sid += "-";
      sid += parts[j];
     }
  }

//+------------------------------------------------------------------+
void RetrackOpenPositions()
  {
   // On (re)attach, re-add any live TG- positions so close journaling/MAE-MFE resume.
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(!IsTGPositionSelected())
         continue;
      ulong tk = g_pos.Ticket();
      if(TrackFind(tk) >= 0)
         continue;
      string tag, sid;
      int tpIdx;
      ParseComment(g_pos.Comment(), tag, tpIdx, sid);
      string symbol = g_pos.Symbol();
      string dir = (g_pos.PositionType() == POSITION_TYPE_BUY) ? "BUY" : "SELL";
      double open = g_pos.PriceOpen();
      int j = TrackAppendSlot();
      g_trkTicket[j]      = tk;
      g_trkSignalId[j]    = sid;
      g_trkChannel[j]     = tag;
      g_trkTpIndex[j]     = tpIdx;
      g_trkMagic[j]       = (ulong)g_pos.Magic();
      g_trkSymbol[j]      = symbol;
      g_trkDir[j]         = dir;
      g_trkReqEntry[j]    = open;
      g_trkRangeLo[j]     = 0.0;
      g_trkRangeHi[j]     = 0.0;
      g_trkReqSl[j]       = g_pos.StopLoss();
      g_trkReqTp[j]       = g_pos.TakeProfit();
      g_trkVolume[j]      = g_pos.Volume();
      g_trkFill[j]        = open;
      g_trkSlippage[j]    = 0.0;
      g_trkOpenTime[j]    = (datetime)g_pos.Time();
      g_trkMinPrice[j]    = open;
      g_trkMaxPrice[j]    = open;
      g_trkMaeCcy[j]      = 0.0;
      g_trkMfeCcy[j]      = 0.0;
      g_trkCloseReason[j] = "";
      Print("[TradinGo] retrack ticket=", tk, " ", symbol, " ", dir,
            " sid=", sid, " tp_index=", tpIdx);
     }
  }

//+------------------------------------------------------------------+
void UpdateExcursions()
  {
   for(int i = 0; i < ArraySize(g_trkTicket); i++)
     {
      if(!PositionSelectByTicket(g_trkTicket[i]))
         continue;
      string sym = g_trkSymbol[i];
      double mark = (g_trkDir[i] == "BUY") ? SymbolInfoDouble(sym, SYMBOL_BID)
                                           : SymbolInfoDouble(sym, SYMBOL_ASK);
      if(mark > 0.0)
        {
         if(g_trkMinPrice[i] <= 0.0 || mark < g_trkMinPrice[i])
            g_trkMinPrice[i] = mark;
         if(mark > g_trkMaxPrice[i])
            g_trkMaxPrice[i] = mark;
        }
      double fl = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      if(fl < g_trkMaeCcy[i])
         g_trkMaeCcy[i] = fl;
      if(fl > g_trkMfeCcy[i])
         g_trkMfeCcy[i] = fl;
     }
  }

//+------------------------------------------------------------------+
void WriteJournalClose(const int i)
  {
   string symbol = g_trkSymbol[i];
   int dg = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   if(dg <= 0)
      dg = 5;
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(point <= 0.0)
      point = _Point;
   ulong posId = g_trkTicket[i];
   double closePrice = 0.0, realized = 0.0;
   datetime closeTime = 0;
   if(HistorySelectByPosition(posId))
     {
      int total = HistoryDealsTotal();
      for(int d = 0; d < total; d++)
        {
         ulong dl = HistoryDealGetTicket(d);
         if(dl == 0)
            continue;
         long entry = HistoryDealGetInteger(dl, DEAL_ENTRY);
         if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY || entry == DEAL_ENTRY_INOUT)
           {
            realized += HistoryDealGetDouble(dl, DEAL_PROFIT)
                      + HistoryDealGetDouble(dl, DEAL_SWAP)
                      + HistoryDealGetDouble(dl, DEAL_COMMISSION);
            datetime tt = (datetime)HistoryDealGetInteger(dl, DEAL_TIME);
            if(tt >= closeTime)
              {
               closeTime = tt;
               closePrice = HistoryDealGetDouble(dl, DEAL_PRICE);
              }
           }
        }
     }
   // close_reason: preset by killswitch, else price heuristic vs requested tp/sl/entry.
   string reason = g_trkCloseReason[i];
   if(reason == "")
     {
      double tol = (double)InpRangeTolerancePoints * point;
      if(tol <= 0.0)
         tol = 50.0 * point;
      if(g_trkReqTp[i] > 0.0 && MathAbs(closePrice - g_trkReqTp[i]) <= tol)
         reason = "TP";
      else if(g_trkReqSl[i] > 0.0 && MathAbs(closePrice - g_trkReqSl[i]) <= tol)
        {
         if(g_trkReqEntry[i] > 0.0 && MathAbs(g_trkReqSl[i] - g_trkReqEntry[i]) <= tol)
            reason = "BE_SL";
         else
            reason = "SL";
        }
      else
         reason = "UNKNOWN";
     }
   double maePrice = (g_trkDir[i] == "BUY") ? g_trkMinPrice[i] : g_trkMaxPrice[i];
   double mfePrice = (g_trkDir[i] == "BUY") ? g_trkMaxPrice[i] : g_trkMinPrice[i];
   string f[29];
   f[0]  = "CLOSE";
   f[1]  = IsoUtc(TimeGMT());
   f[2]  = g_trkSignalId[i];
   f[3]  = g_trkChannel[i];
   f[4]  = IntegerToString(g_trkTpIndex[i]);
   f[5]  = IntegerToString((long)g_trkTicket[i]);
   f[6]  = IntegerToString((long)g_trkMagic[i]);
   f[7]  = symbol;
   f[8]  = g_trkDir[i];
   f[9]  = DoubleToString(g_trkReqEntry[i], dg);
   f[10] = DoubleToString(g_trkRangeLo[i], dg);
   f[11] = DoubleToString(g_trkRangeHi[i], dg);
   f[12] = DoubleToString(g_trkReqSl[i], dg);
   f[13] = DoubleToString(g_trkReqTp[i], dg);
   f[14] = DoubleToString(g_trkVolume[i], 2);
   f[15] = DoubleToString(g_trkFill[i], dg);
   f[16] = DoubleToString(g_trkSlippage[i], 1);
   f[17] = IsoUtc(g_trkOpenTime[i]);
   f[18] = (closeTime > 0) ? IsoUtc(closeTime) : IsoUtc(TimeGMT());
   f[19] = DoubleToString(closePrice, dg);
   f[20] = reason;
   f[21] = DoubleToString(realized, 2);
   f[22] = DoubleToString(maePrice, dg);
   f[23] = DoubleToString(mfePrice, dg);
   f[24] = DoubleToString(g_trkMaeCcy[i], 2);
   f[25] = DoubleToString(g_trkMfeCcy[i], 2);
   f[26] = DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2);
   f[27] = DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2);
   f[28] = DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2);
   AppendCsvLine(JournalPath("trades\\trades_" + YmdUtc(TimeGMT()) + ".csv"),
                 TG_JOURNAL_HEADER, JoinCsv(f));
   Print("[TradinGo] JOURNAL_CLOSE ticket=", g_trkTicket[i], " ", symbol,
         " reason=", reason, " pnl=", DoubleToString(realized, 2));
  }

//+------------------------------------------------------------------+
void DetectClosedPositions()
  {
   for(int i = ArraySize(g_trkTicket) - 1; i >= 0; i--)
     {
      if(PositionSelectByTicket(g_trkTicket[i]))
         continue; // still open
      WriteJournalClose(i);
      TrackRemoveAt(i);
     }
  }

//+------------------------------------------------------------------+
void CloseBucketPositions(const string bucket, const string reason)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(!IsTGPositionSelected())
         continue;
      if(SymbolBucket(g_pos.Symbol()) != bucket)
         continue;
      ulong tk = g_pos.Ticket();
      SetTrackReason(tk, reason);
      ClosePositionKeepMagic(tk);
     }
  }

//+------------------------------------------------------------------+
void CheckFloatingKillSwitch()
  {
   if(InpMaxFloatingLossUSD <= 0.0)
      return;
   string bks[];
   double sums[];
   int nb = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(!IsTGPositionSelected())
         continue;
      string b = SymbolBucket(g_pos.Symbol());
      double v = g_pos.Profit() + g_pos.Swap();
      int idx = -1;
      for(int k = 0; k < nb; k++)
         if(bks[k] == b)
           {
            idx = k;
            break;
           }
      if(idx < 0)
        {
         ArrayResize(bks, nb + 1);
         ArrayResize(sums, nb + 1);
         bks[nb] = b;
         sums[nb] = 0.0;
         idx = nb;
         nb++;
        }
      sums[idx] += v;
     }
   for(int k = 0; k < nb; k++)
     {
      if(sums[k] >= -InpMaxFloatingLossUSD)
         continue;
      if(KsCooldownActive(bks[k]))
         continue;
      Print("[TradinGo] KILLSWITCH_FLOATING bucket=", bks[k],
            " floating=", DoubleToString(sums[k], 2),
            " limit=", DoubleToString(-InpMaxFloatingLossUSD, 2),
            " ccy=", AccountInfoString(ACCOUNT_CURRENCY),
            " -> closing bucket, cooldown=",
            (InpKillSwitchCooldownMin > 0
             ? IntegerToString(InpKillSwitchCooldownMin) + "m" : "off"));
      CloseBucketPositions(bks[k], "KILLSWITCH_FLOATING");
      KsSetCooldown(bks[k]);
     }
  }

//+------------------------------------------------------------------+
void CheckMaxHolding()
  {
   if(InpMaxHoldingMinutes <= 0)
      return;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(!IsTGPositionSelected())
         continue;
      double mins = (double)(TimeCurrent() - (datetime)g_pos.Time()) / 60.0;
      if(mins < (double)InpMaxHoldingMinutes)
         continue;
      ulong tk = g_pos.Ticket();
      double profit = g_pos.Profit() + g_pos.Swap();
      Print("[TradinGo] KILLSWITCH_TIME ticket=", tk, " held=", DoubleToString(mins, 1),
            "m >= ", InpMaxHoldingMinutes, "m profit=", DoubleToString(profit, 2),
            (profit > 0.0 ? " -> BE" : " -> close"));
      if(profit > 0.0)
         ApplyBreakEvenSL(tk);
      else
        {
         SetTrackReason(tk, "KILLSWITCH_TIME");
         ClosePositionKeepMagic(tk);
        }
     }
  }

//+------------------------------------------------------------------+
void CheckHeartbeat()
  {
   if(InpHeartbeatMaxAgeSec <= 0)
      return;
   string content;
   if(!ReadJsonFileQuiet(InpHeartbeatFile, content))
     {
      if(!g_bridgeStale)
         Print("[TradinGo] HEARTBEAT missing file=", InpHeartbeatFile,
               " -> bridge STALE, opens blocked");
      g_bridgeStale = true;
      return;
     }
   datetime hb = ParseIso8601(JsonGetString(content, "ts_utc"));
   if(hb <= 0)
     {
      if(!g_bridgeStale)
         Print("[TradinGo] HEARTBEAT unparseable ts_utc -> bridge STALE, opens blocked");
      g_bridgeStale = true;
      return;
     }
   long age = (long)(TimeGMT() - hb);
   if(age > InpHeartbeatMaxAgeSec)
     {
      if(!g_bridgeStale)
         Print("[TradinGo] HEARTBEAT stale age=", age, "s > ", InpHeartbeatMaxAgeSec,
               "s -> opens blocked");
      g_bridgeStale = true;
     }
   else
     {
      if(g_bridgeStale)
         Print("[TradinGo] HEARTBEAT fresh age=", age, "s -> bridge OK, opens allowed");
      g_bridgeStale = false;
     }
  }

//+------------------------------------------------------------------+
void SampleEquity()
  {
   if(InpEquitySampleSec <= 0)
      return;
   datetime now = TimeGMT();
   if(g_lastEquitySample != 0 && (now - g_lastEquitySample) < InpEquitySampleSec)
      return;
   g_lastEquitySample = now;
   double flTotal = 0.0, flMetals = 0.0, lots = 0.0;
   int cnt = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!g_pos.SelectByIndex(i))
         continue;
      if(!IsTGPositionSelected())
         continue;
      double v = g_pos.Profit() + g_pos.Swap();
      flTotal += v;
      lots += g_pos.Volume();
      cnt++;
      if(SymbolBucket(g_pos.Symbol()) == "METALS")
         flMetals += v;
     }
   string f[9];
   f[0] = IsoUtc(now);
   f[1] = DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2);
   f[2] = DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2);
   f[3] = DoubleToString(flTotal, 2);
   f[4] = DoubleToString(flMetals, 2);
   f[5] = IntegerToString(cnt);
   f[6] = DoubleToString(lots, 2);
   f[7] = DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2);
   f[8] = DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_LEVEL), 2);
   AppendCsvLine(JournalPath("equity\\equity_" + YmdUtc(now) + ".csv"),
                 "ts_utc,balance,equity,floating_total,floating_metals,open_positions_count,total_lots,margin_used,margin_level\n",
                 JoinCsv(f));
  }

//+------------------------------------------------------------------+
double ComputeATR(const string symbol, const ENUM_TIMEFRAMES tf, const int period)
  {
   MqlRates r[];
   int n = CopyRates(symbol, tf, 0, period + 1, r);
   if(n < 2)
      return 0.0;
   double sum = 0.0;
   int cnt = 0;
   for(int i = 1; i < n; i++)
     {
      double tr = MathMax(r[i].high - r[i].low,
                          MathMax(MathAbs(r[i].high - r[i - 1].close),
                                  MathAbs(r[i].low - r[i - 1].close)));
      sum += tr;
      cnt++;
     }
   return (cnt > 0) ? sum / cnt : 0.0;
  }

//+------------------------------------------------------------------+
string RatesToJson(const MqlRates &r[], const int cnt, const int dg)
  {
   string s = "[";
   for(int i = 0; i < cnt; i++)
     {
      if(i > 0)
         s += ",";
      s += "{\"t\":\"" + IsoUtc(r[i].time) + "\",\"o\":" + DoubleToString(r[i].open, dg) +
           ",\"h\":" + DoubleToString(r[i].high, dg) + ",\"l\":" + DoubleToString(r[i].low, dg) +
           ",\"c\":" + DoubleToString(r[i].close, dg) +
           ",\"tick_volume\":" + IntegerToString((long)r[i].tick_volume) + "}";
     }
   return s + "]";
  }

//+------------------------------------------------------------------+
string SessionByGmtHour(const int h)
  {
   bool london = (h >= 7 && h < 16);
   bool ny = (h >= 12 && h < 21);
   bool asia = (h >= 23 || h < 8);
   if(london && ny)
      return "LONDON_NY_OVERLAP";
   if(london)
      return "LONDON";
   if(ny)
      return "NY";
   if(asia)
      return "ASIA";
   return "OFF";
  }

//+------------------------------------------------------------------+
void WriteMarketContext(const string symbol, const string sid)
  {
   if(sid == "")
      return;
   for(int k = 0; k < ArraySize(g_ctxDone); k++)
      if(g_ctxDone[k] == sid)
         return;
   int nc = ArraySize(g_ctxDone);
   ArrayResize(g_ctxDone, nc + 1);
   g_ctxDone[nc] = sid;

   MqlRates m1[], m15[], h1[], d1[];
   int n1  = CopyRates(symbol, PERIOD_M1, 0, 60, m1);
   int n15 = CopyRates(symbol, PERIOD_M15, 0, 20, m15);
   int nh1 = CopyRates(symbol, PERIOD_H1, 0, 10, h1);
   int nd  = CopyRates(symbol, PERIOD_D1, 0, 2, d1);
   if(n1 < 0)
      n1 = 0;
   if(n15 < 0)
      n15 = 0;
   if(nh1 < 0)
      nh1 = 0;

   int dg = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   if(dg <= 0)
      dg = 5;
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(point <= 0.0)
      point = _Point;
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double mid = (bid + ask) / 2.0;
   double spreadPts = (point > 0.0) ? (ask - bid) / point : 0.0;
   double atr15 = ComputeATR(symbol, PERIOD_M15, 14);
   double atr1h = ComputeATR(symbol, PERIOD_H1, 14);

   double dayHi = 0, dayLo = 0, prevHi = 0, prevLo = 0, prevClose = 0;
   if(nd >= 1)
     {
      dayHi = d1[nd - 1].high;
      dayLo = d1[nd - 1].low;
     }
   if(nd >= 2)
     {
      prevHi = d1[nd - 2].high;
      prevLo = d1[nd - 2].low;
      prevClose = d1[nd - 2].close;
     }

   MqlDateTime g;
   TimeToStruct(TimeGMT(), g);
   string session = SessionByGmtHour(g.hour);

   string js = "{";
   js += "\"signal_id\":\"" + sid + "\",";
   js += "\"symbol\":\"" + symbol + "\",";
   js += "\"ts_utc\":\"" + IsoUtc(TimeGMT()) + "\",";
   js += "\"session\":\"" + session + "\",";
   js += "\"gmt_hour\":" + IntegerToString(g.hour) + ",";
   js += "\"bid\":" + DoubleToString(bid, dg) + ",";
   js += "\"ask\":" + DoubleToString(ask, dg) + ",";
   js += "\"spread_points\":" + DoubleToString(spreadPts, 1) + ",";
   js += "\"atr14_m15\":" + DoubleToString(atr15, dg) + ",";
   js += "\"atr14_h1\":" + DoubleToString(atr1h, dg) + ",";
   js += "\"day_high\":" + DoubleToString(dayHi, dg) + ",";
   js += "\"day_low\":" + DoubleToString(dayLo, dg) + ",";
   js += "\"prev_day_high\":" + DoubleToString(prevHi, dg) + ",";
   js += "\"prev_day_low\":" + DoubleToString(prevLo, dg) + ",";
   js += "\"prev_day_close\":" + DoubleToString(prevClose, dg) + ",";
   js += "\"dist_points\":{";
   js += "\"day_high\":" + DoubleToString((dayHi - mid) / point, 1) + ",";
   js += "\"day_low\":" + DoubleToString((mid - dayLo) / point, 1) + ",";
   js += "\"prev_day_high\":" + DoubleToString((prevHi - mid) / point, 1) + ",";
   js += "\"prev_day_low\":" + DoubleToString((mid - prevLo) / point, 1) + ",";
   js += "\"prev_day_close\":" + DoubleToString((mid - prevClose) / point, 1);
   js += "},";
   js += "\"bars_m1\":" + RatesToJson(m1, n1, dg) + ",";
   js += "\"bars_m15\":" + RatesToJson(m15, n15, dg) + ",";
   js += "\"bars_h1\":" + RatesToJson(h1, nh1, dg);
   js += "}\n";

   string path = JournalPath("context\\ctx_" + SanitizeName(sid) + ".json");
   int h = FileOpen(path, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
     {
      Print("[TradinGo] context write failed path=", path, " err=", GetLastError());
      return;
     }
   FileWriteString(h, js);
   FileClose(h);
   Print("[TradinGo] CONTEXT written ", path);
  }

//+------------------------------------------------------------------+
void RefreshInstanceLock()
  {
   if(InpSingleInstanceLock)
      GlobalVariableSet(TG_LOCK_NAME, (double)TimeCurrent());
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   // Single-instance lock: refuse to start if another live instance holds the lock.
   if(InpSingleInstanceLock && GlobalVariableCheck(TG_LOCK_NAME))
     {
      double ts = GlobalVariableGet(TG_LOCK_NAME);
      long age = (long)(TimeCurrent() - (datetime)ts);
      if(age < InpLockStaleSec)
        {
         Print("[TradinGo] INIT_FAILED single-instance lock held (age=", age,
               "s < stale=", InpLockStaleSec, "s). Another TG_TradinGoEA is running. ",
               "If this is stale, delete GlobalVariable '", TG_LOCK_NAME, "'.");
         return INIT_FAILED;
        }
      Print("[TradinGo] stale lock detected (age=", age, "s >= ", InpLockStaleSec,
            "s) -> reclaiming");
     }
   if(InpSingleInstanceLock)
      GlobalVariableSet(TG_LOCK_NAME, (double)TimeCurrent());

   ParseChannels();
   DdInit();
   CheckHaltFlag();
   g_trade.SetDeviationInPoints(InpMaxSlippagePoints);
   EventSetMillisecondTimer(InpPollMs);
   if(InpUseAbsolutePath && StringLen(InpSignalsPath) >= 2 &&
      StringGetCharacter(InpSignalsPath, 1) == ':')
     {
      Print("[TradinGo] WARNING: InpUseAbsolutePath with drive-letter path will fail ",
            "FileOpen (MT5 sandbox, err=5004). Use junction under MQL5\\Files ",
            "and set InpUseAbsolutePath=false, InpSignalsPath=tradingo\\");
     }
   Print("[TradinGo] EA v", EA_VERSION, " started | channels=", g_channelCount,
         " path=", (StringLen(InpSignalsPath) > 0 ? InpSignalsPath : "<MQL5\\Files>"),
         " abs=", InpUseAbsolutePath,
         " range_tolerance_pts=", InpRangeTolerancePoints,
         " oro_tolerance_pts=", InpOroRangeTolerancePoints,
         " ignore_existing_on_init=", InpIgnoreExistingOnInit,
         " stack_opens=", InpStackOpensIfFlatBusy,
         " (requires JSON allow_stack)",
         " reentry_max_drift_pct_of_sl=", DoubleToString(InpReentryMaxDriftPctOfSl, 1),
         " protect_existing_levels=", InpProtectExistingLevels,
         " naked_fallback_sl_pts=", InpNakedFallbackSlPoints,
         " be_never_worse_than_entry=", InpBeNeverWorseThanEntry,
         " be_pending_retry=", InpBePendingRetry,
         " stop_buffer_pts=", InpStopBufferPoints);
   Print("[TradinGo] v", EA_VERSION, " lots/tags | ivan=", DoubleToString(InpLotIvan, 2),
         "/", InpTagIvan,
         " stark=", DoubleToString(InpLotStark, 2), "/", InpTagStark,
         " comment_tg_prefix=", InpCommentUseTgPrefix);
   Print("[TradinGo] v", EA_VERSION, " hardening | killswitch currency=ACCOUNT_CURRENCY (",
         AccountInfoString(ACCOUNT_CURRENCY), ")",
         " max_floating_loss=", DoubleToString(InpMaxFloatingLossUSD, 2),
         " cooldown_min=", InpKillSwitchCooldownMin,
         " max_holding_min=", InpMaxHoldingMinutes,
         " heartbeat_max_age_sec=", InpHeartbeatMaxAgeSec,
         " hb_file=", InpHeartbeatFile,
         " equity_sample_sec=", InpEquitySampleSec,
         " journal_prefix=", InpJournalPrefix,
         " single_instance_lock=", InpSingleInstanceLock,
         " lock_stale_sec=", InpLockStaleSec);
   Print("[TradinGo] v", EA_VERSION, " prop guards | dd_max_pct=", DoubleToString(InpDdMaxPct, 2),
         " dd_close_at_pct=", DoubleToString(InpDdCloseAtPct, 1),
         " dd_block_new_at_pct=", DoubleToString(InpDdBlockNewAtPct, 1),
         " dd_recovery_lot=", DoubleToString(InpDdRecoveryLot, 2),
         " halt_flag_file=", (StringLen(InpHaltFlagFile) > 0 ? InpHaltFlagFile : "<off>"),
         " kill_switch=", (g_haltFlag ? "ON" : "off"),
         " dd_sizing=", InpDdSizingEnabled,
         " dd_utilization_pct=", DoubleToString(InpDdUtilizationPct, 1),
         " dd_step_pct=", DoubleToString(InpDdStepPct, 1),
         " dd_max_lot=", DoubleToString(InpDdMaxLot, 2),
         " max_concurrent_lots=", DoubleToString(InpMaxConcurrentLots, 2));
   for(int i = 0; i < g_channelCount; i++)
      Print("[TradinGo]  watch ", g_channelFile[i]);
   if(InpIgnoreExistingOnInit)
      SeedAndClearExistingSignals();
   RetrackOpenPositions();
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   if(InpSingleInstanceLock)
      GlobalVariableDel(TG_LOCK_NAME);
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   RefreshInstanceLock();
   CheckHaltFlag();
   CheckHeartbeat();
   for(int i = 0; i < g_channelCount; i++)
      PollChannel(i);
   DetectClosedPositions();
   SampleEquity();
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   ProcessBePending();
   CheckEquityFloorGuard();
   CheckFloatingKillSwitch();
   CheckMaxHolding();
   UpdateExcursions();
  }
//+------------------------------------------------------------------+
