//+------------------------------------------------------------------+
//| TG_TradinGoLevels.mq5                                            |
//| Disegna supporti / resistenze / liquidita' estratti dalle        |
//| analisi dei canali (file CSV scritto da levels_store.py).        |
//| Solo grafica: non apre, modifica o chiude ordini.                |
//+------------------------------------------------------------------+
#property copyright "TG TradinGo"
#property version   "1.00"
#property indicator_chart_window
#property indicator_plots 0

input string InpLevelsFile        = "tradingo\\tradingo_levels.csv";
input int    InpRefreshSec        = 30;
input color  InpColorHybridGold   = clrGold;
input color  InpColorHybridForex  = clrDeepSkyBlue;
input color  InpColorSertorio     = clrOrchid;
input color  InpColorOther        = clrSilver;
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
ENUM_LINE_STYLE KindStyle(const string kind)
  {
   if(kind == "resistance") return STYLE_DASH;
   if(kind == "liquidity")  return STYLE_DOT;
   return STYLE_SOLID;
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
void DrawLevel(const int idx, const string src, const string kind,
               const double price, const double priceTo, const string label)
  {
   color c = SourceColor(src);
   string name = LV_PREFIX + IntegerToString(idx);
   if(priceTo > 0.0)
     {
      datetime t1 = iTime(_Symbol, PERIOD_D1, 5);
      datetime t2 = TimeCurrent() + 86400 * 3;
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, price, t2, priceTo);
      ObjectSetInteger(0, name, OBJPROP_FILL, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
     }
   else
     {
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, kind == "liquidity" ? 1 : 2);
     }
   ObjectSetInteger(0, name, OBJPROP_COLOR, c);
   ObjectSetInteger(0, name, OBJPROP_STYLE, KindStyle(kind));
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   string tip = SourceName(src) + " | " + kind + " " + DoubleToString(price, _Digits)
                + (priceTo > 0.0 ? "-" + DoubleToString(priceTo, _Digits) : "")
                + (label != "" ? " | " + label : "");
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tip);
   if(InpShowLabels)
     {
      string tname = name + "_T";
      ObjectCreate(0, tname, OBJ_TEXT, 0, TimeCurrent(), priceTo > 0.0 ? priceTo : price);
      ObjectSetString(0, tname, OBJPROP_TEXT, "  " + kind + (label != "" ? " " + label : ""));
      ObjectSetInteger(0, tname, OBJPROP_COLOR, c);
      ObjectSetInteger(0, tname, OBJPROP_FONTSIZE, 8);
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
   LegendLine(row++, "── supporto   - - resistenza   ··· liquidita'", clrDarkGray);
  }

//+------------------------------------------------------------------+
void Reload()
  {
   ObjectsDeleteAll(0, LV_PREFIX);
   ArrayResize(g_sources, 0);
   int h = FileOpen(InpLevelsFile, FILE_READ | FILE_CSV | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE, ',');
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
   int h = FileOpen(InpLevelsFile, FILE_READ | FILE_BIN | FILE_SHARE_READ | FILE_SHARE_WRITE);
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
