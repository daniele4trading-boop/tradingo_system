//+------------------------------------------------------------------+
//| TG_TradinGoEA.mq4                                                |
//| Legge gli stessi signal_ch_*.json del bridge Python (contratto   |
//| EA_SPEC / bridge 2.16) ed esegue ordini su MT4.                  |
//| Non fa parsing Telegram: solo esecuzione JSON.                   |
//+------------------------------------------------------------------+
#property copyright "TradinGo"
#property link      "https://github.com/daniele4trading-boop/tradingo_system"
#property version   "1.12"
#property strict
#property description "JSON signal executor for TG TradinGo bridge (MT4)"

#define EA_VERSION "1.12"
#define MAX_CHANNELS 16
#define MAX_TRADES_PER_SIGNAL 5

// Default: read under MQL4\\Files (bridge writes there or via junction).
// Prefer junction: MQL4\\Files\\tradingo -> signals folder, InpSignalsPath=tradingo\\
input string InpSignalsPath             = "tradingo\\";
input bool   InpUseAbsolutePath         = false;
input string InpChannels                = "gold,forex,oro,stark,ivan";
input string InpSymbolSuffix            = "";
input double InpLotMultiplier           = 1.0;
input double InpAddLotFactor            = 0.5;
input double InpLotIvan                 = 0.01;
input double InpLotStark                = 0.01;
input double InpLotGold                 = 0.01;
input double InpLotOro                  = 0.01;
input double InpLotForex                = 0.01;
input string InpTagIvan                 = "IT";
input string InpTagStark                = "AS";
input string InpTagGold                 = "SG";
input string InpTagOro                  = "OR";
input string InpTagForex                = "FX";
input bool   InpCommentUseTgPrefix      = false;
input int    InpMaxSlippagePoints       = 50;
input int    InpPollMs                  = 500;
input int    InpRangeTolerancePoints    = 200;
input int    InpOroRangeTolerancePoints = 250;
input bool   InpLogCancelledSignals     = true;
input bool   InpClearSignalAfterProcess = true;
input bool   InpIgnoreExistingOnInit    = true;
input bool   InpStackOpensIfFlatBusy    = true;
input int    InpStopBufferPoints        = 20;
// Off-market guard: skip an open whose entry/SL/TP are farther than this % from
// the current price (channel posting stale or wrong-scale levels). 0 = off.
input double InpMaxLevelDeviationPct     = 2.0;
// Re-entry drift guard: an inherited re-entry keeps the SL of the original
// setup, so if the market already moved toward that SL the risk/reward is no
// longer the published one. Skip the re-entry when the drift from the signal
// entry exceeds this % of the entry->SL distance. 0 = off.
input double InpReentryMaxDriftPctOfSl   = 40.0;
// A new setup arriving while positions are open (allow_stack=false) modifies
// their SL/TP: never apply a TP on the losing side of the position's open price,
// nor an SL already crossed by the market (would liquidate at once).
input bool   InpProtectExistingLevels    = true;
// Naked open ("Gold sell now"): the levels arrive in a later message, so the
// orders would stay unprotected until then. Open with a provisional SL this
// many points away, replaced by the real one on UPDATE_OPEN. 0 = no SL.
input int    InpNakedFallbackSlPoints    = 1200;
// Break-even commands must never make an order worse: when SL=entry is not
// legal (order in loss, or entry closer than the broker stops level) keep the
// current SL instead of clamping past the entry.
input bool   InpBeNeverWorseThanEntry    = true;
// When SL=entry is not legal yet (price still within the stops level of the
// entry) the break-even stays pending and is retried on every tick until the
// market allows it; an order that closes meanwhile is simply dropped.
input bool   InpBePendingRetry           = true;
// UPDATE_SL moving the stop further away is a legitimate channel decision (give
// the price room), but the lot size was computed on the previous risk: cap the
// new stop at this multiple of the open->current SL distance instead of
// following it without limit. 0 = no cap (follow the channel exactly).
input double InpMaxSlWidenFactor         = 2.0;
// Seconds within which orders opened together count as one batch
// (CLOSE_SELECTIVE keep=ALL_BUT_NEWEST closes only the newest batch).
input int    InpBatchWindowSec           = 120;
input int    InpMagicOffset             = 0;
input int    InpHeartbeatMaxAgeSec      = 180;
input string InpHeartbeatFile           = "tradingo_heartbeat.json";

string g_channelSuffix[MAX_CHANNELS];
string g_channelFile[MAX_CHANNELS];
string g_lastTimestamp[MAX_CHANNELS];
int    g_channelCount = 0;
bool   g_bridgeStale  = false;
datetime g_lastPoll   = 0;

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
      int c = StringGetCharacter(json, v);
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
bool WriteTextFileContent(const string path, const string content)
  {
   int flags = FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE;
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
bool ReadSignalFile(const string fileName, string &content)
  {
   string candidates[4];
   int n = 0;
   if(InpUseAbsolutePath && StringLen(InpSignalsPath) > 0)
      candidates[n++] = BuildAbsoluteSignalPath(fileName);
   candidates[n++] = BuildRelativeSignalPath(fileName);
   candidates[n++] = fileName;
   for(int i = 0; i < n; i++)
     {
      if(ReadTextFileContent(candidates[i], content))
         return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
bool ClearSignalFile(const string fileName)
  {
   string payload = "{\n  \"action\": \"NONE\"\n}\n";
   if(InpUseAbsolutePath && StringLen(InpSignalsPath) > 0)
     {
      if(WriteTextFileContent(BuildAbsoluteSignalPath(fileName), payload))
         return true;
     }
   if(WriteTextFileContent(BuildRelativeSignalPath(fileName), payload))
      return true;
   return WriteTextFileContent(fileName, payload);
  }

//+------------------------------------------------------------------+
string ResolveSymbol(const string raw)
  {
   string s = raw;
   StringToUpper(s);
   if(StringLen(InpSymbolSuffix) > 0 && StringFind(s, InpSymbolSuffix) < 0)
      s += InpSymbolSuffix;
   return s;
  }

//+------------------------------------------------------------------+
int TradeMagic(const int magicBase, const int index)
  {
   return magicBase + index + InpMagicOffset;
  }

//+------------------------------------------------------------------+
bool IsOurOrderMagic(const int magic, const int magicBase, const int maxTrades)
  {
   for(int i = 1; i <= maxTrades; i++)
     {
      if(magic == TradeMagic(magicBase, i))
         return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
bool SelectOurOrder(const int ticket, const int magicBase, const int maxTrades)
  {
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return false;
   if(OrderCloseTime() > 0)
      return false;
   return IsOurOrderMagic(OrderMagicNumber(), magicBase, maxTrades);
  }

//+------------------------------------------------------------------+
int FindOurOrderByMagic(const string symbol, const int magic)
  {
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderSymbol() != symbol)
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(OrderMagicNumber() == magic)
         return OrderTicket();
     }
   return -1;
  }

//+------------------------------------------------------------------+
int CountOurOrders(const string symbol, const int magicBase, const int maxTrades)
  {
   int cnt = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderSymbol() != symbol)
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(IsOurOrderMagic(OrderMagicNumber(), magicBase, maxTrades))
         cnt++;
     }
   return cnt;
  }

//+------------------------------------------------------------------+
void CloseOurOrders(const string symbol, const int magicBase, const int maxTrades)
  {
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderSymbol() != symbol)
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, maxTrades))
         continue;
      int ticket = OrderTicket();
      string sym = OrderSymbol();
      int typ = OrderType();
      double lots = OrderLots();
      double price = (typ == OP_BUY) ? MarketInfo(sym, MODE_BID) : MarketInfo(sym, MODE_ASK);
      if(!OrderClose(ticket, lots, price, InpMaxSlippagePoints, clrRed))
         Print("[TradinGo] OrderClose failed ticket=", ticket, " err=", GetLastError());
     }
  }

//+------------------------------------------------------------------+
double NormalizeLot(const string symbol, double lot)
  {
   double minLot = MarketInfo(symbol, MODE_MINLOT);
   double maxLot = MarketInfo(symbol, MODE_MAXLOT);
   double step   = MarketInfo(symbol, MODE_LOTSTEP);
   if(step <= 0)
      step = 0.01;
   lot *= InpLotMultiplier;
   lot = MathFloor(lot / step + 1e-8) * step;
   if(lot < minLot)
      lot = minLot;
   if(lot > maxLot)
      lot = maxLot;
   return NormalizeDouble(lot, 2);
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
   double point = MarketInfo(symbol, MODE_POINT);
   if(point <= 0.0)
      point = Point;
   int stopsLevel = (int)MarketInfo(symbol, MODE_STOPLEVEL);
   int freezeLevel = (int)MarketInfo(symbol, MODE_FREEZELEVEL);
   int bufferPts = (extraPts >= 0) ? extraPts : InpStopBufferPoints;
   if(bufferPts < 0)
      bufferPts = 0;
   int minPts = MathMax(stopsLevel, freezeLevel) + bufferPts;
   if(minPts < 1)
      minPts = 1;
   double minDist = minPts * point;
   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   double price = (direction == "BUY") ? MarketInfo(symbol, MODE_BID)
                                       : MarketInfo(symbol, MODE_ASK);

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
bool ModifyOrderSLTP(const int ticket, const double sl, const double tp,
                     const int extraPts = -1)
  {
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return false;
   if(OrderCloseTime() > 0)
      return false;
   string symbol = OrderSymbol();
   string direction = (OrderType() == OP_BUY) ? "BUY" : "SELL";
   double curSl = OrderStopLoss();
   double curTp = OrderTakeProfit();
   double nsl = sl > 0 ? sl : curSl;
   double ntp = tp > 0 ? tp : curTp;
   if(nsl > 0.0 || ntp > 0.0)
      AdjustStopsToMinDistance(symbol, direction, nsl, ntp, extraPts);
   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   double pt = MarketInfo(symbol, MODE_POINT);
   if(pt <= 0.0)
      pt = Point;
   nsl = NormalizeDouble(nsl, digits);
   ntp = NormalizeDouble(ntp, digits);
   if(MathAbs(nsl - curSl) < pt * 0.1 && MathAbs(ntp - curTp) < pt * 0.1)
      return true;
   bool ok = OrderModify(ticket, OrderOpenPrice(), nsl, ntp, 0, clrBlue);
   if(!ok)
      Print("[TradinGo] OrderModify failed ticket=", ticket,
            " err=", GetLastError(),
            " sl=", DoubleToString(nsl, digits),
            " tp=", DoubleToString(ntp, digits));
   return ok;
  }

//+------------------------------------------------------------------+
int g_bePending[];

bool BePendingContains(const int ticket)
  {
   for(int i = 0; i < ArraySize(g_bePending); i++)
      if(g_bePending[i] == ticket)
         return true;
   return false;
  }

void BePendingAdd(const int ticket)
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

bool ApplyBreakEvenSLEx(const int ticket, const bool fromQueue);

bool ApplyBreakEvenSL(const int ticket)
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
      int tk = g_bePending[i];
      if(!OrderSelect(tk, SELECT_BY_TICKET) || OrderCloseTime() > 0)
        {
         BePendingRemove(i);
         continue;
        }
      if(ApplyBreakEvenSLEx(tk, true))
        {
         Print("[TradinGo] BE_PENDING_APPLIED ticket=", tk,
               " sl=", DoubleToString(OrderOpenPrice(), (int)MarketInfo(OrderSymbol(), MODE_DIGITS)));
         BePendingRemove(i);
        }
     }
  }

bool ApplyBreakEvenSLEx(const int ticket, const bool fromQueue)
  {
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return false;
   string symbol = OrderSymbol();
   string direction = (OrderType() == OP_BUY) ? "BUY" : "SELL";
   double be = OrderOpenPrice();
   int buffers[3];
   buffers[0] = InpStopBufferPoints;
   buffers[1] = InpStopBufferPoints + 20;
   buffers[2] = InpStopBufferPoints + 50;
   for(int attempt = 0; attempt < 3; attempt++)
     {
      if(!OrderSelect(ticket, SELECT_BY_TICKET))
         return false;
      double nsl = be;
      double ntp = OrderTakeProfit();
      AdjustStopsToMinDistance(symbol, direction, nsl, ntp, buffers[attempt]);
      double tol = MarketInfo(symbol, MODE_POINT);
      bool worseThanEntry = (direction == "BUY") ? (nsl < be - tol) : (nsl > be + tol);
      if(InpBeNeverWorseThanEntry && worseThanEntry)
        {
         if(fromQueue)
            return false;
         if(InpBePendingRetry)
           {
            BePendingAdd(ticket);
            Print("[TradinGo] BE_PENDING ticket=", ticket,
                  " entry=", DoubleToString(be, (int)MarketInfo(symbol, MODE_DIGITS)),
                  " would_be_sl=", DoubleToString(nsl, (int)MarketInfo(symbol, MODE_DIGITS)),
                  " (", direction, ") — retry until SL=entry is legal");
           }
         else
            Print("[TradinGo] BE_SKIPPED_WORSE_THAN_ENTRY ticket=", ticket,
                  " entry=", DoubleToString(be, (int)MarketInfo(symbol, MODE_DIGITS)),
                  " would_be_sl=", DoubleToString(nsl, (int)MarketInfo(symbol, MODE_DIGITS)),
                  " (", direction, ") — SL kept unchanged");
         return false;
        }
      if(ModifyOrderSLTP(ticket, nsl, 0, buffers[attempt]))
         return true;
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
      if(tpIndex > 0)
         base = StringFormat("%s-T%d", channelTag, tpIndex);
      else
         base = channelTag;
     }
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
                      const int magic)
  {
   if(!InpLogCancelledSignals)
      return;

   string ch = JsonGetString(json, "channel_id");
   string ts = JsonGetString(json, "timestamp");
   double point = MarketInfo(symbol, MODE_POINT);
   if(point <= 0.0)
      point = Point;
   double slippagePts = 0.0;
   if(signalEntry > 0.0 && fillPrice > 0.0)
     {
      slippagePts = (fillPrice - signalEntry) / point;
      if(direction == "SELL")
         slippagePts = -slippagePts;
     }

   string line = StringFormat(
      "%s,%s,%s,%s,%s,%s,%.5f,%.5f,%.5f,%.5f,%.1f,%.1f,%d,%d,%s\n",
      TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
      ch, channelFile, symbol, direction, status,
      signalEntry, rangeLo, rangeHi, fillPrice,
      slippagePts, distancePoints, tpIndex, magic, ts);

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
bool PriceInRange(const string symbol, const string direction,
                  const double lo, const double hi, double &distPoints)
  {
   double price = (direction == "BUY") ? MarketInfo(symbol, MODE_ASK)
                                       : MarketInfo(symbol, MODE_BID);
   double point = MarketInfo(symbol, MODE_POINT);
   if(point <= 0.0)
      point = Point;
   if(price >= lo && price <= hi)
     {
      distPoints = 0.0;
      return true;
     }
   if(price < lo)
      distPoints = (lo - price) / point;
   else
      distPoints = (price - hi) / point;
   return false;
  }

//+------------------------------------------------------------------+
bool TryExecuteWithEntryRange(const string channelFile, const string json,
                              const string symbol, const string direction,
                              const double rangeLo, const double rangeHi,
                              string &entryStatus, double &distPoints)
  {
   int tol = RangeToleranceForChannel(channelFile, json);
   if(PriceInRange(symbol, direction, rangeLo, rangeHi, distPoints))
     {
      entryStatus = "EXECUTED_IN_RANGE";
      return true;
     }
   if(distPoints <= tol)
     {
      entryStatus = "EXECUTED_TOLERANCE";
      return true;
     }
   entryStatus = "CANCELLED_RANGE";
   AppendSignalStat(channelFile, json, symbol, direction, "CANCELLED_RANGE",
                    rangeLo, rangeHi, SignalEntryFromJson(json, rangeLo, rangeHi),
                    0.0, distPoints, 0, 0);
   Print("[TradinGo] SIGNAL_CANCELLED range ", symbol, " distPts=", distPoints,
         " tol=", tol);
   return false;
  }

//+------------------------------------------------------------------+
datetime ParseIso8601(const string s)
  {
   // YYYY-MM-DDTHH:MM:SS (T o spazio); StringToTime vuole "yyyy.mm.dd" e
   // sui trattini restituisce 0, quindi i campi vanno letti a mano.
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
void CheckHeartbeat()
  {
   if(InpHeartbeatMaxAgeSec <= 0)
     {
      g_bridgeStale = false;
      return;
     }
   string content;
   bool ok = false;
   if(InpUseAbsolutePath && StringLen(InpSignalsPath) > 0)
      ok = ReadTextFileContent(BuildAbsoluteSignalPath(InpHeartbeatFile), content);
   if(!ok)
      ok = ReadTextFileContent(BuildRelativeSignalPath(InpHeartbeatFile), content);
   if(!ok)
      ok = ReadTextFileContent(InpHeartbeatFile, content);
   if(!ok)
     {
      if(!g_bridgeStale)
         Print("[TradinGo] HEARTBEAT file mancante (", InpHeartbeatFile,
               ") -> bridge STALE, aperture bloccate");
      g_bridgeStale = true;
      return;
     }
   // Il bridge scrive "ts_utc" (bridge_journal.write_heartbeat); gli altri due
   // nomi restano come fallback difensivo.
   string ts = JsonGetString(content, "ts_utc");
   if(ts == "")
      ts = JsonGetString(content, "ts");
   if(ts == "")
      ts = JsonGetString(content, "timestamp");
   datetime t = ParseIso8601(ts);
   if(t <= 0)
     {
      if(!g_bridgeStale)
         Print("[TradinGo] HEARTBEAT ts_utc illeggibile ('", ts,
               "') -> bridge STALE, aperture bloccate");
      g_bridgeStale = true;
      return;
     }
   // Heartbeat is UTC-ish; compare with TimeGMT when available.
   datetime now = TimeGMT();
   if(now <= 0)
      now = TimeCurrent();
   g_bridgeStale = ((int)(now - t) > InpHeartbeatMaxAgeSec);
  }

//+------------------------------------------------------------------+
bool IsOpenBlocked()
  {
   CheckHeartbeat();
   if(g_bridgeStale)
     {
      Print("[TradinGo] OPEN blocked — bridge heartbeat stale");
      return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Off-market guard: the channel sometimes posts levels of another    |
//| price context ("sell 4039 tp 4030" with gold at 4237). Without     |
//| this check the stops get clamped to the legal side and the order    |
//| opens only to be closed at once, paying the spread.                |
//+------------------------------------------------------------------+
bool LevelsNearMarket(const double price, const double sl, const double tp,
                      const double entry, double &outWorstPct)
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
                const double sl, const double tp, const int magic,
                const string comment,
                const string channelFile, const string json,
                const string entryStatus,
                const double rangeLo, const double rangeHi,
                const double distancePoints, const int tpIndex)
  {
   RefreshRates();
   int cmd = (direction == "BUY") ? OP_BUY : OP_SELL;
   double price = (cmd == OP_BUY) ? MarketInfo(symbol, MODE_ASK)
                                  : MarketInfo(symbol, MODE_BID);
   if(price <= 0.0)
     {
      Print("[TradinGo] no quote for ", symbol);
      return false;
     }
   double devPct = 0.0;
   if(!LevelsNearMarket(price, sl, tp, SignalEntryFromJson(json, rangeLo, rangeHi),
                        devPct))
     {
      Print("[TradinGo] OPEN skipped — off-market levels price=", price,
            " sl=", sl, " tp=", tp,
            " deviation=", DoubleToString(devPct, 2), "%",
            " max=", DoubleToString(InpMaxLevelDeviationPct, 2), "%");
      AppendSignalStat(channelFile, json, symbol, direction, "CANCELLED_OFF_MARKET",
                       rangeLo, rangeHi, SignalEntryFromJson(json, rangeLo, rangeHi),
                       0.0, distancePoints, tpIndex, magic);
      return false;
     }
   double nsl = sl;
   double ntp = tp;
   if(nsl > 0.0 || ntp > 0.0)
      AdjustStopsToMinDistance(symbol, direction, nsl, ntp);
   if(!StopsOnCorrectSide(direction, price, nsl, ntp))
     {
      Print("[TradinGo] OPEN skipped — SL/TP wrong side vs price ", price);
      AppendSignalStat(channelFile, json, symbol, direction, "CANCELLED_BAD_STOPS",
                       rangeLo, rangeHi, SignalEntryFromJson(json, rangeLo, rangeHi),
                       0.0, distancePoints, tpIndex, magic);
      return false;
     }
   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   nsl = (nsl > 0.0) ? NormalizeDouble(nsl, digits) : 0.0;
   ntp = (ntp > 0.0) ? NormalizeDouble(ntp, digits) : 0.0;
   price = NormalizeDouble(price, digits);

   ResetLastError();
   int ticket = OrderSend(symbol, cmd, lot, price, InpMaxSlippagePoints,
                          nsl, ntp, comment, magic, 0, clrDodgerBlue);
   if(ticket < 0)
     {
      // retry without SL/TP then modify (some brokers reject stops on open)
      int err = GetLastError();
      Print("[TradinGo] OrderSend failed err=", err, " — retry bare then modify");
      ResetLastError();
      ticket = OrderSend(symbol, cmd, lot, price, InpMaxSlippagePoints,
                         0, 0, comment, magic, 0, clrDodgerBlue);
      if(ticket < 0)
        {
         Print("[TradinGo] OrderSend retry failed err=", GetLastError(),
               " ", symbol, " ", direction, " lot=", lot);
         AppendSignalStat(channelFile, json, symbol, direction, "OPEN_FAILED",
                          rangeLo, rangeHi, SignalEntryFromJson(json, rangeLo, rangeHi),
                          0.0, distancePoints, tpIndex, magic);
         return false;
        }
      if(nsl > 0.0 || ntp > 0.0)
         ModifyOrderSLTP(ticket, nsl, ntp);
     }

   double fill = price;
   if(OrderSelect(ticket, SELECT_BY_TICKET))
      fill = OrderOpenPrice();
   AppendSignalStat(channelFile, json, symbol, direction, entryStatus,
                    rangeLo, rangeHi, SignalEntryFromJson(json, rangeLo, rangeHi),
                    fill, distancePoints, tpIndex, magic);
   Print("[TradinGo] OPEN ok ticket=", ticket, " ", symbol, " ", direction,
         " lot=", DoubleToString(lot, 2), " magic=", magic);
   return true;
  }

//+------------------------------------------------------------------+
// A re-entry inherits the SL of the original setup: if the market already ate
// part of that stop distance, the trade opens with a fraction of the published
// risk budget.
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
   double price = (direction == "BUY") ? MarketInfo(symbol, MODE_ASK)
                                      : MarketInfo(symbol, MODE_BID);
   if(price <= 0.0)
      return true;
   double driftPct = MathAbs(price - entry) / slDistance * 100.0;
   if(driftPct <= InpReentryMaxDriftPctOfSl)
      return true;
   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   Print("[TradinGo] REENTRY_CANCELLED ", JsonGetString(json, "channel_id"), " ",
         symbol, " ", direction,
         " entry=", DoubleToString(entry, digits),
         " price=", DoubleToString(price, digits),
         " sl=", DoubleToString(sl, digits),
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
                     const double distPoints)
  {
   int n = ArraySize(tps);
   if(n <= 0)
      n = 1;
   if(n > MAX_TRADES_PER_SIGNAL)
      n = MAX_TRADES_PER_SIGNAL;
   string channelTag = ChannelShortTag(channelFile, json);
   bool any = false;
   for(int i = 0; i < n; i++)
     {
      double tp = (i < ArraySize(tps)) ? tps[i] : 0.0;
      int magic = TradeMagic(magicBase, i + 1);
      string comment = BuildTradeComment(channelTag, i + 1, json);
      if(OpenMarket(symbol, direction, lot, sl, tp, magic, comment,
                    channelFile, json, entryStatus, rangeLo, rangeHi,
                    distPoints, i + 1))
         any = true;
     }
   return any;
  }

//+------------------------------------------------------------------+
// Drop levels that would hurt an already open order: a TP behind its open price
// (the order would be "taken profit" at a loss) and an SL the market has already
// crossed (instant liquidation). 0 = leave the current level. Requires the order
// to be selected by the caller.
void FilterAdverseLevels(const int ticket, double &sl, double &tp)
  {
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return;
   string symbol = OrderSymbol();
   bool isBuy = (OrderType() == OP_BUY);
   double openPrice = OrderOpenPrice();
   double bid = MarketInfo(symbol, MODE_BID);
   double ask = MarketInfo(symbol, MODE_ASK);
   int digits = (int)MarketInfo(symbol, MODE_DIGITS);

   if(tp > 0.0)
     {
      bool tpProfitable = isBuy ? (tp > openPrice) : (tp < openPrice);
      if(!tpProfitable)
        {
         Print("[TradinGo] MODIFY_SKIPPED_ADVERSE_TP ticket=", ticket,
               " tp=", DoubleToString(tp, digits),
               " open=", DoubleToString(openPrice, digits),
               " side=", (isBuy ? "BUY" : "SELL"), " - TP kept unchanged");
         tp = 0.0;
        }
     }

   if(sl > 0.0)
     {
      bool slCrossed = isBuy ? (sl >= bid) : (sl <= ask);
      if(slCrossed)
        {
         Print("[TradinGo] MODIFY_SKIPPED_CROSSED_SL ticket=", ticket,
               " sl=", DoubleToString(sl, digits),
               " bid=", DoubleToString(bid, digits),
               " ask=", DoubleToString(ask, digits),
               " side=", (isBuy ? "BUY" : "SELL"), " - SL kept unchanged");
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
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderSymbol() != symbol)
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
         continue;
      double tp = (idx < ArraySize(tps)) ? tps[idx] : 0.0;
      int ticket = OrderTicket();
      double useSl = sl;
      double useTp = tp;
      if(InpProtectExistingLevels)
         FilterAdverseLevels(ticket, useSl, useTp);
      if(ModifyOrderSLTP(ticket, useSl, useTp))
         any = true;
      idx++;
     }
   return any;
  }

//+------------------------------------------------------------------+
// Provisional SL for a naked open, so no order ever sits unprotected while
// waiting for the message with the real levels. 0 when the guard is off.
double NakedFallbackSl(const string symbol, const string direction)
  {
   if(InpNakedFallbackSlPoints <= 0)
      return 0.0;
   double point = MarketInfo(symbol, MODE_POINT);
   if(point <= 0.0)
      point = Point;
   double price = (direction == "BUY") ? MarketInfo(symbol, MODE_ASK)
                  : MarketInfo(symbol, MODE_BID);
   if(price <= 0.0)
      return 0.0;
   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   double dist = InpNakedFallbackSlPoints * point;
   double sl = NormalizeDouble((direction == "BUY") ? price - dist : price + dist, digits);
   Print("[TradinGo] NAKED_FALLBACK_SL ", symbol, " ", direction,
         " price=", DoubleToString(price, digits),
         " sl=", DoubleToString(sl, digits),
         " pts=", InpNakedFallbackSlPoints);
   return sl;
  }

//+------------------------------------------------------------------+
bool HandleOpen(const string channelFile, const string json)
  {
   string action = JsonGetString(json, "action");
   string direction = JsonGetString(json, "direction");
   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   if(IsOpenBlocked())
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
      fixedLot = 0.01;
   double ov = LotOverrideForChannel(channelFile, json);
   if(ov > 0.0)
      fixedLot = ov;
   double lot = NormalizeLot(symbol, fixedLot);
   if(JsonGetBool(json, "is_add_signal"))
      lot = NormalizeLot(symbol, fixedLot * InpAddLotFactor);

   if(JsonGetBool(json, "inherit_from_first"))
     {
      for(int i = OrdersTotal() - 1; i >= 0; i--)
        {
         if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
            continue;
         if(OrderSymbol() != symbol)
            continue;
         if(OrderType() != OP_BUY && OrderType() != OP_SELL)
            continue;
         if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
            continue;
         if(sl <= 0)
            sl = OrderStopLoss();
         if(ArraySize(tps) == 0)
           {
            ArrayResize(tps, 1);
            tps[0] = OrderTakeProfit();
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
      int n = trades;
      if(n <= 0)
         n = 1;
      if(n > MAX_TRADES_PER_SIGNAL)
         n = MAX_TRADES_PER_SIGNAL;
      double emptyTps[];
      ArrayResize(emptyTps, n);
      for(int i = 0; i < n; i++)
         emptyTps[i] = 0.0;
      double nakedSl = NakedFallbackSl(symbol, direction);
      return OpenSplitTrades(symbol, direction, lot, nakedSl, emptyTps, magicBase,
                             channelFile, json, "EXECUTED_OPEN_NOW", rangeLo, rangeHi,
                             distPoints);
     }

   // Align trades count with tp_levels when present
   if(ArraySize(tps) > 0)
      trades = ArraySize(tps);
   if(trades > MAX_TRADES_PER_SIGNAL)
      trades = MAX_TRADES_PER_SIGNAL;
   if(ArraySize(tps) < trades)
     {
      int old = ArraySize(tps);
      ArrayResize(tps, trades);
      for(int k = old; k < trades; k++)
         tps[k] = 0.0;
     }

   int openCnt = CountOurOrders(symbol, magicBase, MAX_TRADES_PER_SIGNAL);
   if(openCnt > 0)
     {
      bool allowStack = InpStackOpensIfFlatBusy && JsonGetBool(json, "allow_stack");
      if(!allowStack)
        {
         Print("[TradinGo] OPEN skipped — ", openCnt,
               " order(s) exist, modifying SL/TP instead");
         return ModifyExistingTrades(symbol, magicBase, sl, tps);
        }
      Print("[TradinGo] OPEN stack — ", openCnt, " order(s) already open");
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
      fixedLot = 0.01;
   double ovUpd = LotOverrideForChannel(channelFile, json);
   if(ovUpd > 0.0)
      fixedLot = ovUpd;
   double lot = NormalizeLot(symbol, fixedLot);

   int openCnt = CountOurOrders(symbol, magicBase, MAX_TRADES_PER_SIGNAL);
   if(openCnt == 0)
     {
      // levels_only: the bridge salvaged SL/TP from a message whose entry zone
      // was unusable (channel typo), so they may only modify open orders.
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
      if(IsOpenBlocked())
         return false;
      Print("[TradinGo] UPDATE_OPEN but no positions — opening fresh");
      return OpenSplitTrades(symbol, direction, lot, sl, tps, magicBase,
                             channelFile, json, entryStatus, rangeLo, rangeHi, distPoints);
     }

   // Fill/modify by magic: never close+reopen. Missing slots → OPEN fill;
   // extras beyond planned N → SL only (TP unchanged).
   string channelTag = ChannelShortTag(channelFile, json);
   int n = trades;
   if(n <= 0)
      n = MathMax(1, ArraySize(tps));
   if(n > MAX_TRADES_PER_SIGNAL)
      n = MAX_TRADES_PER_SIGNAL;

   for(int i = 1; i <= n; i++)
     {
      int magic = TradeMagic(magicBase, i);
      int ticket = FindOurOrderByMagic(symbol, magic);
      double tp = (i - 1 < ArraySize(tps)) ? tps[i - 1] : 0.0;
      if(ticket > 0)
         ModifyOrderSLTP(ticket, sl, tp);
      else
        {
         if(IsOpenBlocked())
            continue;
         Print("[TradinGo] UPDATE_OPEN fill missing magic=", magic,
               " slot=", i, "/", n);
         OpenMarket(symbol, direction, lot, sl, tp, magic,
                    BuildTradeComment(channelTag, i, json),
                    channelFile, json, "EXECUTED_UPDATE_FILL", 0, 0, 0, i);
        }
     }

   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderSymbol() != symbol)
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, 8))
         continue;
      int mg = OrderMagicNumber();
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
      ModifyOrderSLTP(OrderTicket(), sl, 0);
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
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderSymbol() != symbol)
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
         continue;
      if(tpIndex > 0 && OrderMagicNumber() != TradeMagic(magicBase, tpIndex))
         continue;
      int ticket = OrderTicket();
      double useSl = 0.0;
      double useTp = newTp;
      if(InpProtectExistingLevels)
         FilterAdverseLevels(ticket, useSl, useTp);
      if(useTp <= 0.0)
         continue;
      if(ModifyOrderSLTP(ticket, 0, useTp))
         Print("[TradinGo] UPDATE_TP ticket=", ticket,
               " tp=", DoubleToString(useTp, (int)MarketInfo(symbol, MODE_DIGITS)),
               " tp_index=", (tpIndex > 0 ? IntegerToString(tpIndex) : "all"));
     }
   return true;
  }

//+------------------------------------------------------------------+
// Caps an SL that widens the risk at InpMaxSlWidenFactor times the current
// open->SL distance of the selected order. Returns the SL to apply.
double CapWidenedSl(const string symbol, const double newSl)
  {
   double curSl = OrderStopLoss();
   if(!InpProtectExistingLevels || InpMaxSlWidenFactor <= 0.0 ||
      curSl <= 0.0 || newSl <= 0.0)
      return newSl;
   double open = OrderOpenPrice();
   bool isBuy = (OrderType() == OP_BUY);
   if(isBuy ? (newSl >= curSl) : (newSl <= curSl))
      return newSl;
   double risk = MathAbs(open - curSl);
   if(risk <= 0.0)
      return newSl;
   double maxRisk = risk * InpMaxSlWidenFactor;
   if(MathAbs(open - newSl) <= maxRisk)
      return newSl;
   int dg = (int)MarketInfo(symbol, MODE_DIGITS);
   double capped = NormalizeDouble(isBuy ? (open - maxRisk) : (open + maxRisk), dg);
   Print("[TradinGo] UPDATE_SL_WIDEN_CAPPED ticket=", OrderTicket(),
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
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderSymbol() != symbol)
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
         continue;
      // Lo stop che si allontana viene seguito, ma non oltre il tetto: la size
      // e' stata calcolata sul rischio precedente.
      double useSl = CapWidenedSl(symbol, newSl);
      ModifyOrderSLTP(OrderTicket(), useSl, 0);
     }
   return true;
  }

//+------------------------------------------------------------------+
bool HandleCheckAndClose(const string json)
  {
   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   string direction = JsonGetString(json, "direction");
   int magicBase = JsonGetInt(json, "magic_base");
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderSymbol() != symbol)
         continue;
      if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
         continue;
      string pdir = (OrderType() == OP_BUY) ? "BUY" : "SELL";
      if(pdir != direction)
         continue;
      int ticket = OrderTicket();
      double lots = OrderLots();
      double price = (OrderType() == OP_BUY) ? MarketInfo(symbol, MODE_BID)
                                             : MarketInfo(symbol, MODE_ASK);
      if(!OrderClose(ticket, lots, price, InpMaxSlippagePoints, clrRed))
         Print("[TradinGo] CHECK_AND_CLOSE failed ticket=", ticket, " err=", GetLastError());
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
      for(int i = OrdersTotal() - 1; i >= 0; i--)
        {
         if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
            continue;
         if(OrderType() != OP_BUY && OrderType() != OP_SELL)
            continue;
         if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
            continue;
         int ticket = OrderTicket();
         string sym = OrderSymbol();
         double lots = OrderLots();
         double price = (OrderType() == OP_BUY) ? MarketInfo(sym, MODE_BID)
                                                : MarketInfo(sym, MODE_ASK);
         if(!OrderClose(ticket, lots, price, InpMaxSlippagePoints, clrRed))
            Print("[TradinGo] CLOSE_ALL_SYMBOL close failed ticket=", ticket,
                  " err=", GetLastError());
        }
      return true;
     }
   CloseOurOrders(ResolveSymbol(symRaw), magicBase, MAX_TRADES_PER_SIGNAL);
   return true;
  }

//+------------------------------------------------------------------+
//| CLOSE_SELECTIVE keep=ALL_BUT_NEWEST: chiude solo l'ultimo blocco   |
//| aperto ("Chiudo la rientry"), lasciando il setup principale.       |
//| Blocco = ordini aperti entro InpBatchWindowSec dal più recente. Se |
//| tutti gli ordini sono nello stesso blocco non c'è nessun rientro   |
//| da chiudere e il comando non fa nulla.                             |
//+------------------------------------------------------------------+
bool CloseNewestBatch(const string json)
  {
   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   int magicBase = JsonGetInt(json, "magic_base");

   datetime newest = 0;
   datetime oldest = 0;
   int total = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderSymbol() != symbol)
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
         continue;
      datetime t = OrderOpenTime();
      if(total == 0 || t > newest)
         newest = t;
      if(total == 0 || t < oldest)
         oldest = t;
      total++;
     }
   if(total == 0)
     {
      Print("[TradinGo] CLOSE_SELECTIVE keep=ALL_BUT_NEWEST ", symbol,
            " — nessun ordine nostro aperto");
      return true;
     }

   int window = (InpBatchWindowSec > 0 ? InpBatchWindowSec : 120);
   if((int)(newest - oldest) <= window)
     {
      Print("[TradinGo] CLOSE_SELECTIVE keep=ALL_BUT_NEWEST ", symbol,
            " ignorata — un solo blocco aperto (", total, " ordini)");
      return true;
     }

   int closed = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderSymbol() != symbol)
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
         continue;
      if((int)(newest - OrderOpenTime()) > window)
         continue;
      int ticket = OrderTicket();
      double lots = OrderLots();
      double cprice = (OrderType() == OP_BUY) ? MarketInfo(symbol, MODE_BID)
                                              : MarketInfo(symbol, MODE_ASK);
      if(OrderClose(ticket, lots, cprice, InpMaxSlippagePoints, clrRed))
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

   for(int pass = 0; pass < 2; pass++)
     {
      int side = (pass == 0 ? OP_BUY : OP_SELL);
      double keepPrice = 0.0;
      bool found = false;
      for(int i = OrdersTotal() - 1; i >= 0; i--)
        {
         if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
            continue;
         if(OrderSymbol() != symbol || OrderType() != side)
            continue;
         if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
            continue;
         double price = OrderOpenPrice();
         bool keepHigher = (keep == "HIGHEST") || (keep == "BEST" && side == OP_SELL);
         if(!found || (keepHigher ? price > keepPrice : price < keepPrice))
           {
            keepPrice = price;
            found = true;
           }
        }
      if(!found)
         continue;

      double tol = MarketInfo(symbol, MODE_POINT) * 10.0;
      int closed = 0;
      for(int i = OrdersTotal() - 1; i >= 0; i--)
        {
         if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
            continue;
         if(OrderSymbol() != symbol || OrderType() != side)
            continue;
         if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
            continue;
         if(MathAbs(OrderOpenPrice() - keepPrice) <= tol)
            continue;
         int ticket = OrderTicket();
         double lots = OrderLots();
         double cprice = (side == OP_BUY) ? MarketInfo(symbol, MODE_BID)
                                          : MarketInfo(symbol, MODE_ASK);
         if(OrderClose(ticket, lots, cprice, InpMaxSlippagePoints, clrRed))
            closed++;
         else
            Print("[TradinGo] CLOSE_SELECTIVE close failed ticket=", ticket,
                  " err=", GetLastError());
        }
      Print("[TradinGo] CLOSE_SELECTIVE ", symbol,
            " side=", (side == OP_BUY ? "BUY" : "SELL"),
            " keep=", keep,
            " keep_price=", DoubleToString(keepPrice, Digits),
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
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderSymbol() != symbol)
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
         continue;
      ModifyOrderSLTP(OrderTicket(), be, 0);
     }
   return true;
  }

//+------------------------------------------------------------------+
bool HandleCloseHalfBe(const string json)
  {
   string symbol = ResolveSymbol(JsonGetString(json, "symbol"));
   int magicBase = JsonGetInt(json, "magic_base");
   Print("[TradinGo] CLOSE_HALF_BE ", symbol, " — close 50% + SL to entry");
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderSymbol() != symbol)
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
         continue;
      int ticket = OrderTicket();
      double vol = OrderLots();
      double half = NormalizeLot(symbol, vol / 2.0);
      double minLot = MarketInfo(symbol, MODE_MINLOT);
      if(half >= minLot && half < vol)
        {
         double cprice = (OrderType() == OP_BUY) ? MarketInfo(symbol, MODE_BID)
                                                 : MarketInfo(symbol, MODE_ASK);
         if(!OrderClose(ticket, half, cprice, InpMaxSlippagePoints, clrOrange))
            Print("[TradinGo] CLOSE_HALF partial failed ticket=", ticket,
                  " err=", GetLastError());
         else
            Print("[TradinGo] CLOSE_HALF ok ticket=", ticket,
                  " closed=", DoubleToString(half, 2));
        }
      ApplyBreakEvenSL(ticket);
     }
   return true;
  }

//+------------------------------------------------------------------+
bool HandleCheckAndBe(const string json)
  {
   int magicBase = JsonGetInt(json, "magic_base");
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(!IsOurOrderMagic(OrderMagicNumber(), magicBase, MAX_TRADES_PER_SIGNAL))
         continue;
      ApplyBreakEvenSL(OrderTicket());
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
   int targetMagic = TradeMagic(magicBase, tpIndex);
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      if(OrderMagicNumber() != targetMagic)
         continue;
      int ticket = OrderTicket();
      string sym = OrderSymbol();
      double lots = OrderLots();
      double price = (OrderType() == OP_BUY) ? MarketInfo(sym, MODE_BID)
                                             : MarketInfo(sym, MODE_ASK);
      if(!OrderClose(ticket, lots, price, InpMaxSlippagePoints, clrRed))
         Print("[TradinGo] CHECK_AND_CLOSE_TP close failed ticket=", ticket,
               " err=", GetLastError());
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

//+------------------------------------------------------------------+
int OnInit()
  {
   ParseChannels();
   if(g_channelCount <= 0)
     {
      Print("[TradinGo] ERROR: no channels in InpChannels");
      return INIT_FAILED;
     }

   if(InpUseAbsolutePath && StringLen(InpSignalsPath) >= 2 &&
      StringGetCharacter(InpSignalsPath, 1) == ':')
     {
      Print("[TradinGo] WARN: absolute drive path may fail FileOpen on MT4 sandbox. ",
            "Prefer junction under MQL4\\Files and InpUseAbsolutePath=false, ",
            "InpSignalsPath=tradingo\\");
     }

   Print("[TradinGo] EA v", EA_VERSION, " (MT4) started | channels=", g_channelCount,
         " path=", (StringLen(InpSignalsPath) > 0 ? InpSignalsPath : "<MQL4\\Files>"),
         " abs=", (InpUseAbsolutePath ? "true" : "false"));
   Print("[TradinGo] v", EA_VERSION, " lots | ivan=", DoubleToString(InpLotIvan, 2),
         " stark=", DoubleToString(InpLotStark, 2),
         " gold=", DoubleToString(InpLotGold, 2),
         " oro=", DoubleToString(InpLotOro, 2),
         " forex=", DoubleToString(InpLotForex, 2));

   if(InpIgnoreExistingOnInit)
      SeedAndClearExistingSignals();

   EventSetMillisecondTimer(MathMax(200, InpPollMs));
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   for(int i = 0; i < g_channelCount; i++)
      PollChannel(i);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   ProcessBePending();
   // Backup poll if timer is delayed (timer is primary)
   datetime now = TimeCurrent();
   if(g_lastPoll > 0 && (now - g_lastPoll) * 1000 < InpPollMs)
      return;
   g_lastPoll = now;
   for(int i = 0; i < g_channelCount; i++)
      PollChannel(i);
  }
//+------------------------------------------------------------------+
