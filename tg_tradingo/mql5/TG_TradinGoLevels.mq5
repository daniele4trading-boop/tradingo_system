//+------------------------------------------------------------------+
//| TG_TradinGoLevels.mq5                                            |
//| Disegna supporti / resistenze / liquidita' estratti dalle        |
//| analisi dei canali (file CSV scritto da levels_store.py).        |
//| Solo grafica: non apre, modifica o chiude ordini.                |
//+------------------------------------------------------------------+
#property copyright "TG TradinGo"
#property version   "1.10"
#property indicator_chart_window
#property indicator_plots 0

input string InpLevelsFile        = "tradingo\\tradingo_levels.csv";
input int    InpRefreshSec        = 30;
input bool   InpCommonFolder      = true;   // file in Terminal\\Common\\Files (uno per tutti i terminali)
input color  InpColorHybridGold   = clrGold;
input color  InpColorHybridForex  = clrDeepSkyBlue;
input color  InpColorSertorio     = clrOrchid;
input color  InpColorOther        = clrSilver;
input bool   InpKindColors        = true;   // supporti/resistenze colorati per tipo, non per canale
input color  InpColorSupport      = clrMediumSeaGreen;
input color  InpColorResistance   = clrIndianRed;
input int    InpZoneOpacity       = 25;     // % di colore delle zone (0 = invisibile, 100 = pieno)
input bool   InpShowLabels        = true;
input bool   InpShowLegend        = true;
input ENUM_BASE_CORNER InpLegendCorner = CORNER_LEFT_UPPER;
input int    InpLegendFontSize    = 9;

#define LV_PREFIX "TGLV_"

string   g_sources[];
datetime g_lastMod = 0;

//+------------------------------------------------------------------+
color SourceColor(const string src)
  {
   if(src == "HYBRID_GOLD")  return InpColorHybridGold;
   if(src == "HYBRID_FOREX") return InpColorHybridForex;
   if(src == "SERTORIO")     return InpColorSertorio;
   return InpColorOther;
  }

//+------------------------------------------------------------------+
string SourceName(const string src)
  {
   if(src == "HYBRID_GOLD")  return "Hybrid Setup Gold";
   if(src == "HYBRID_FOREX") return "Hybrid Setup Forex";
   if(src == "SERTORIO")     return "Matteo Sertorio";
   return src;
  }

//+------------------------------------------------------------------+
string SourceTag(const string src)
  {
   if(src == "HYBRID_GOLD")  return "Hybrid G";
   if(src == "HYBRID_FOREX") return "Hybrid FX";
   if(src == "SERTORIO")     return "Sertorio";
   return src;
  }

//+------------------------------------------------------------------+
bool IsSR(const string kind)
  {
   return (kind == "support" || kind == "resistance");
  }

//+------------------------------------------------------------------+
color LevelColor(const string src, const string kind)
  {
   if(InpKindColors && kind == "support")    return InpColorSupport;
   if(InpKindColors && kind == "resistance") return InpColorResistance;
   return SourceColor(src);
  }

//+------------------------------------------------------------------+
color Blend(const color c, const color bg, const int pct)
  {
   int p = MathMax(0, MathMin(100, pct));
   int r = ((int)(c & 0xFF) * p + (int)(bg & 0xFF) * (100 - p)) / 100;
   int g = ((int)((c >> 8) & 0xFF) * p + (int)((bg >> 8) & 0xFF) * (100 - p)) / 100;
   int b = ((int)((c >> 16) & 0xFF) * p + (int)((bg >> 16) & 0xFF) * (100 - p)) / 100;
   return (color)(r | (g << 8) | (b << 16));
  }

//+------------------------------------------------------------------+
ENUM_LINE_STYLE KindStyle(const string kind)
  {
   if(kind == "resistance") return STYLE_DASH;
   if(kind == "liquidity")  return STYLE_DOT;
   if(StringFind(kind, "watch") == 0) return STYLE_DASHDOTDOT;
   return STYLE_SOLID;
  }

//+------------------------------------------------------------------+
string KindName(const string kind)
  {
   if(kind == "support")    return "supporto";
   if(kind == "resistance") return "resistenza";
   if(kind == "liquidity")  return "liquidita'";
   if(kind == "key")        return "livello chiave";
   if(kind == "watch_buy")  return "BUY in osservazione";
   if(kind == "watch_sell") return "SELL in osservazione";
   return "in osservazione";
  }

//+------------------------------------------------------------------+
bool SymbolMatches(const string lvSymbol)
  {
   string chart = _Symbol;
   StringToUpper(chart);
   return (StringLen(lvSymbol) > 0 && StringFind(chart, lvSymbol) == 0);
  }

//+------------------------------------------------------------------+
void AddSource(const string src)
  {
   for(int i = 0; i < ArraySize(g_sources); i++)
      if(g_sources[i] == src)
         return;
   int n = ArraySize(g_sources);
   ArrayResize(g_sources, n + 1);
   g_sources[n] = src;
  }

//+------------------------------------------------------------------+
void SetCommon(const string name, const color c, const string tip)
  {
   ObjectSetInteger(0, name, OBJPROP_COLOR, c);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tip);
  }

//+------------------------------------------------------------------+
void DrawLevel(const int idx, const string src, const string kind,
               const double price, const double priceTo, const string label)
  {
   color c = LevelColor(src, kind);
   string name = LV_PREFIX + IntegerToString(idx);
   string tip = SourceName(src) + " | " + KindName(kind) + " " + DoubleToString(price, _Digits)
                + (priceTo > 0.0 ? "-" + DoubleToString(priceTo, _Digits) : "")
                + (label != "" ? " | " + label : "");
   if(priceTo > 0.0)
     {
      datetime t1 = iTime(_Symbol, PERIOD_D1, 5);
      datetime t2 = TimeCurrent() + 86400 * 3;
      if(IsSR(kind) || kind == "liquidity")
        {
         color bg = (color)ChartGetInteger(0, CHART_COLOR_BACKGROUND);
         ObjectCreate(0, name + "_F", OBJ_RECTANGLE, 0, t1, price, t2, priceTo);
         ObjectSetInteger(0, name + "_F", OBJPROP_FILL, true);
         SetCommon(name + "_F", Blend(c, bg, InpZoneOpacity), tip);
        }
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, price, t2, priceTo);
      ObjectSetInteger(0, name, OBJPROP_FILL, false);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
     }
   else
     {
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, kind == "key" ? 2 : 1);
     }
   SetCommon(name, c, tip);
   ObjectSetInteger(0, name, OBJPROP_STYLE, KindStyle(kind));
   if(InpShowLabels)
     {
      string tname = name + "_T";
      string txt = label != "" ? label : KindName(kind);
      ObjectCreate(0, tname, OBJ_TEXT, 0, TimeCurrent(), priceTo > 0.0 ? MathMax(price, priceTo) : price);
      ObjectSetString(0, tname, OBJPROP_TEXT, "  " + SourceTag(src) + " · " + txt);
      ObjectSetInteger(0, tname, OBJPROP_COLOR, c);
      ObjectSetInteger(0, tname, OBJPROP_FONTSIZE, 7);
      ObjectSetInteger(0, tname, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
      ObjectSetInteger(0, tname, OBJPROP_SELECTABLE, false);
     }
  }

//+------------------------------------------------------------------+
void LegendLine(const int row, const string text, const color c)
  {
   string name = LV_PREFIX + "LEG_" + IntegerToString(row);
   ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, InpLegendCorner);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, 20 + row * (InpLegendFontSize + 8));
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, c);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, InpLegendFontSize);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
  }

//+------------------------------------------------------------------+
void DrawLegend(const int count)
  {
   if(!InpShowLegend)
      return;
   int row = 0;
   LegendLine(row++, "TG TradinGo livelli (" + IntegerToString(count) + ")", clrWhite);
   for(int i = 0; i < ArraySize(g_sources); i++)
      LegendLine(row++, "■ " + SourceName(g_sources[i]), SourceColor(g_sources[i]));
   if(InpKindColors)
     {
      LegendLine(row++, "── supporto", InpColorSupport);
      LegendLine(row++, "- - resistenza", InpColorResistance);
     }
   else
      LegendLine(row++, "── supporto   - - resistenza", clrDarkGray);
   LegendLine(row++, "··· liquidita'   ━━ livello chiave   -··- in osservazione", clrDarkGray);
  }

//+------------------------------------------------------------------+
void Reload()
  {
   ObjectsDeleteAll(0, LV_PREFIX);
   ArrayResize(g_sources, 0);
   int h = FileOpen(InpLevelsFile, FILE_READ | FILE_CSV | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE | (InpCommonFolder ? FILE_COMMON : 0), ',');
   int count = 0;
   if(h != INVALID_HANDLE)
     {
      bool header = true;
      datetime nowUtc = TimeGMT();
      while(!FileIsEnding(h))
        {
         string f[7];
         int n = 0;
         while(n < 7)
           {
            f[n++] = FileReadString(h);
            if(FileIsLineEnding(h) || FileIsEnding(h))
               break;
           }
         while(!FileIsLineEnding(h) && !FileIsEnding(h))
            FileReadString(h);
         if(header) { header = false; continue; }
         if(n < 4)
            continue;
         string sym = f[0];
         StringToUpper(sym);
         if(!SymbolMatches(sym))
            continue;
         if(n >= 6 && f[5] != "" && StringToTime(f[5]) <= nowUtc)
            continue;
         double price = StringToDouble(f[3]);
         double priceTo = (n >= 5 && f[4] != "") ? StringToDouble(f[4]) : 0.0;
         if(price <= 0.0)
            continue;
         DrawLevel(count++, f[1], f[2], price, priceTo, n >= 7 ? f[6] : "");
         AddSource(f[1]);
        }
      FileClose(h);
     }
   DrawLegend(count);
   ChartRedraw();
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   Reload();
   EventSetTimer(MathMax(5, InpRefreshSec));
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   ObjectsDeleteAll(0, LV_PREFIX);
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   long mod = 0;
   int h = FileOpen(InpLevelsFile, FILE_READ | FILE_BIN | FILE_SHARE_READ | FILE_SHARE_WRITE | (InpCommonFolder ? FILE_COMMON : 0));
   if(h != INVALID_HANDLE)
     {
      mod = FileGetInteger(h, FILE_MODIFY_DATE);
      FileClose(h);
     }
   if((datetime)mod != g_lastMod)
     {
      g_lastMod = (datetime)mod;
      Reload();
     }
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated, const int begin,
                const double &price[])
  {
   return rates_total;
  }
