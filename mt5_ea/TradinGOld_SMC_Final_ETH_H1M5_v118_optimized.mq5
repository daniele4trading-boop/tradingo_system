//+------------------------------------------------------------------+
//|  TradinGOld_SMC_Final_ETH_H1M5_v118_optimized.mq5              |
//|  Strategia: ETH SMC H1 setup -> M5 trigger                    |
//|  SL minimo bps + TP multipli 0.8/1.2/2.0R                     |
//+------------------------------------------------------------------+
#property copyright "Daniele - Doppio Zero Trading"
#property version   "1.18"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

//------------------------------------------------------------------
//  INPUT
//------------------------------------------------------------------
enum ENUM_PRESET_MODE { PRESET_AUTO, PRESET_CUSTOM, PRESET_GOLD, PRESET_BTC, PRESET_ETH };
enum ENUM_TRIGGER_MODE { TRIGGER_TICK_BODY, TRIGGER_M1_RANGE_BODY };

input group "=== GESTIONE RISCHIO ==="
input double InpRiskPct      = 1.0;    // Rischio % del balance per trade
input double InpMaxSpreadPts = 30.0;   // Spread massimo (punti simbolo)
input double InpMaxSpreadBps = 0.0;    // Spread massimo in bps; usato con sizing bps
input double InpMinSLPts     = 200.0;  // SL minimo forzato (punti o bps)
input double InpMaxSLPts     = 500.0;  // SL massimo; 0 = disabilitato
input double InpSLBufferPts  = 1.0;    // Buffer SL oltre HH1/LL1 (punti o bps)
input bool   InpUseBpsSizing = true;   // ETH: SL/buffer/body in bps
input bool   InpUsePartialClose = true; // Chiude parziali sui target
input double InpTP1_R        = 0.80;   // TP1 in multipli di rischio
input double InpTP1_ClosePct = 50.0;   // Percentuale iniziale da chiudere a TP1
input double InpTP2_R        = 1.20;   // TP2 in multipli di rischio
input double InpTP2_ClosePct = 30.0;   // Percentuale iniziale da chiudere a TP2
input double InpTP3_R        = 2.00;   // TP finale del runner
input bool   InpUseBreakEven = true;   // Porta SL a BE dopo trigger
input double InpBE_Trigger_R = 0.80;   // Trigger BE in multipli di rischio

input group "=== PARAMETRI STRATEGIA ==="
input int    InpMaxBarsAfterReversal= 80; // Barre M5 massime per trovare BOS
input int    InpStructSearchBars = 80;    // Barre M5 per cercare swing strutturale
input int    InpSwingLeftBars    = 2;     // Barre a sinistra per swing M5
input int    InpSwingRightBars   = 2;     // Barre a destra per swing M5
input int    InpMaxBarsAfterSwing= 20;    // Barre M5 max tra HH1/LL1 e reversal
input double InpMinReversalBodyPts = 10.0;// Corpo minimo candela reversal
input double InpMinReversalBodyRatio = 0.45; // Corpo/range minimo reversal
input int    InpReversalAvgBars = 10;     // Media corpi per filtro impulso
input double InpReversalAvgMult = 1.1;    // Corpo reversal > media corpi * mult
input int    InpBTSSearchBars    = 30;    // Barre M5 per cercare ultima BTS/STB
input double InpMinBTSBodyPts    = 5.0;   // Corpo minimo BTS/STB; se piccolo cerca la precedente
input double InpMinBTSBodyRatio  = 0.35;  // Corpo/range minimo BTS/STB anti-spike
input bool   InpBOSRequiresClose = false; // true: BOS solo con close oltre livello
input ENUM_TRIGGER_MODE InpTriggerMode = TRIGGER_M1_RANGE_BODY; // Range M1 aumenta aperture
input double InpTriggerTolerance = 0.0; // Tolleranza trigger: punti o bps secondo preset

input group "=== LIQUIDITA / CONFLUENZE ==="
input bool   InpUseLiquidityZones    = true;  // Usa FVG/OB come confluence
input bool   InpRequireLiquidityZone = true;  // true: setup valido solo su FVG/OB
input int    InpFVG_H1_Bars          = 300;   // Barre H1 per FVG
input int    InpOB_M15_Bars          = 200;   // Barre M15 per OB
input double InpOB_ImpMult           = 1.2;   // Moltiplicatore impulso OB

input group "=== TIMEFRAME ETH ==="
input ENUM_TIMEFRAMES InpSetupTF   = PERIOD_H1;  // Struttura/reversal/BOS
input ENUM_TIMEFRAMES InpTriggerTF = PERIOD_M5;  // Trigger range/body
input ENUM_TIMEFRAMES InpFvgTF     = PERIOD_H4;  // FVG higher timeframe
input ENUM_TIMEFRAMES InpObTF      = PERIOD_H1;  // OB timeframe

input group "=== SESSIONI ==="
input bool   InpFilterSession = false;
input int    InpSessStart    = 0;
input int    InpSessEnd      = 24;

input group "=== EA ==="
input ENUM_PRESET_MODE InpPresetMode = PRESET_ETH;  // Versione specifica ETH
input long   InpMagic        = 20260001;
input bool   InpShowLines    = true;
input bool   InpAlerts       = false;

//------------------------------------------------------------------
//  STRUTTURE
//------------------------------------------------------------------
enum ENUM_LTYPE { LT_HIGH, LT_LOW };
enum ENUM_LSRC  { LS_FVG,  LS_OB  };
enum ENUM_DIR   { D_SHORT, D_LONG  };
enum ENUM_STATE { S_SEEK,  S_BOS,  S_TRIG };

struct Zone
{
   datetime   time;
   double     top, bot, mid;
   ENUM_LTYPE ltype;
   ENUM_LSRC  src;
   bool       consumed;
};

//------------------------------------------------------------------
//  GLOBALI
//------------------------------------------------------------------
CTrade     g_trade;
ENUM_STATE g_state = S_SEEK;
ENUM_DIR   g_dir;
ENUM_PRESET_MODE g_active_preset = PRESET_CUSTOM;

// --- Parametri effettivi dopo applicazione preset ---
bool       g_use_bps_sizing = false;
bool       g_use_liquidity_zones = true;
bool       g_require_liquidity_zone = true;
double     g_risk_pct = 1.0;
double     g_max_spread_pts = 30.0;
double     g_max_spread_bps = 0.0;
double     g_min_sl_limit = 3.0;
double     g_max_sl_limit = 1000.0;
double     g_sl_buffer_limit = 30.0;
double     g_take_profit_r = 2.0;
double     g_tp1_r = 0.8;
double     g_tp1_close_pct = 50.0;
double     g_tp2_r = 1.2;
double     g_tp2_close_pct = 30.0;
double     g_tp3_r = 2.0;
double     g_be_trigger_r = 0.8;
bool       g_use_partial_close = true;
bool       g_use_break_even = true;
double     g_min_reversal_body_limit = 10.0;
double     g_min_bts_body_limit = 5.0;
ENUM_TRIGGER_MODE g_trigger_mode = TRIGGER_M1_RANGE_BODY;
double     g_trigger_tolerance = 0.0;
ENUM_TIMEFRAMES g_setup_tf = PERIOD_H1;
ENUM_TIMEFRAMES g_trigger_tf = PERIOD_M5;
ENUM_TIMEFRAMES g_fvg_tf = PERIOD_H4;
ENUM_TIMEFRAMES g_ob_tf = PERIOD_H1;

// --- Dati reversal / struttura M5 ---
datetime   g_sweep_time;
double     g_sweep_hi;
double     g_sweep_lo;
double     g_sw_body_top;
double     g_sw_body_bot;
double     g_protected_extreme; // short: HH1 | long: LL1
double     g_bos_level;         // short: ultimo swing low | long: ultimo swing high
datetime   g_bos_level_time;
datetime   g_anchor_time;       // short: HH1 | long: LL1
double     g_anchor_price;
datetime   g_last_setup_anchor_time = 0;

// --- Zone di liquidita FVG/OB ---
Zone       g_zones[];
int        g_nz = 0;
datetime   g_ob_consumed[];
int        g_ob_consumed_n = 0;
bool       g_liquidity_ok = false;
ENUM_LSRC  g_liquidity_src;
datetime   g_liquidity_time = 0;
double     g_liquidity_top = 0;
double     g_liquidity_bot = 0;

// --- Dati BOS / trigger ---
datetime   g_bos_time;
double     g_trigger_top;       // corpo ultima BTS/STB M5
double     g_trigger_bot;
datetime   g_trigger_time;

// --- Bar tracking ---
datetime   g_last_m5 = 0;
datetime   g_last_m1 = 0;

//------------------------------------------------------------------
//  INIT / DEINIT
//------------------------------------------------------------------
int OnInit()
{
   ConfigurePreset();
   if(g_tp1_r <= 0.0 || g_tp2_r <= 0.0 || g_tp3_r <= 0.0 ||
      g_tp2_r < g_tp1_r || g_tp3_r < g_tp2_r)
   {
      Alert("Parametri TP non validi: TP1 <= TP2 <= TP3 e tutti > 0");
      return INIT_FAILED;
   }
   if(g_tp1_close_pct < 0.0 || g_tp2_close_pct < 0.0 ||
      g_tp1_close_pct + g_tp2_close_pct > 100.0)
   {
      Alert("Percentuali TP non valide");
      return INIT_FAILED;
   }

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(30);
   g_trade.SetTypeFilling(ORDER_FILLING_IOC);

   ArrayResize(g_zones, 0);
   ArrayResize(g_ob_consumed, 0);
   g_nz = 0;
   g_ob_consumed_n = 0;
   g_state = S_SEEK;
   if(g_use_liquidity_zones)
      RebuildZones();

   Print("SMC ETH H1/M5 v1.18 avviato | ", _Symbol,
         " | preset=", PresetName(g_active_preset),
         " | setup=", EnumToString(g_setup_tf),
         " | triggerTF=", EnumToString(g_trigger_tf),
         " | sizing=", (g_use_bps_sizing ? "bps" : "points"),
         " | Risk=", g_risk_pct, "% | SL min=", g_min_sl_limit,
         (g_use_bps_sizing ? " bps" : " pt"),
         " | SL buffer=", g_sl_buffer_limit,
         (g_use_bps_sizing ? " bps" : " pt"),
         " | TP1=", g_tp1_r, "R(", g_tp1_close_pct, "%)",
         " | TP2=", g_tp2_r, "R(", g_tp2_close_pct, "%)",
         " | TP3=", g_tp3_r, "R | BE=", g_be_trigger_r, "R",
         " | trigger=", TriggerModeName(g_trigger_mode),
         " | chart TF=", EnumToString((ENUM_TIMEFRAMES)Period()));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(InpShowLines)
      ObjectsDeleteAll(0, "SMC_");
}

//------------------------------------------------------------------
//  ON TICK
//------------------------------------------------------------------
void OnTick()
{
   ManageOpenPositions();

   if(InpFilterSession && !IsInSession())
      return;

   datetime cur_m5 = iTime(_Symbol, g_setup_tf, 0);
   if(cur_m5 != g_last_m5)
   {
      g_last_m5 = cur_m5;
      if(g_use_liquidity_zones)
         RebuildZones();
      OnBarM5();
   }

   datetime cur_m1 = iTime(_Symbol, g_trigger_tf, 0);
   if(cur_m1 != g_last_m1)
   {
      g_last_m1 = cur_m1;
      OnBarM1();
   }

   // Il trigger puo essere tick-esatto o retest della zona tramite range M1.
   if(g_state == S_TRIG)
      CheckTrigger();
}

//------------------------------------------------------------------
//  NUOVA BARRA M5
//------------------------------------------------------------------
void OnBarM5()
{
   if(!IsSpreadAllowed())
      return;

   switch(g_state)
   {
      case S_SEEK: DoSeek();   break;
      case S_BOS:  DoBOS();    break;
      case S_TRIG: DoTrigM5(); break;
   }
}

//------------------------------------------------------------------
//  STATE: SEEK - cerca cambio struttura M5 dopo HH1/LL1
//------------------------------------------------------------------
void DoSeek()
{
   double hi = iHigh(_Symbol,  g_setup_tf, 1);
   double lo = iLow(_Symbol,   g_setup_tf, 1);
   double op = iOpen(_Symbol,  g_setup_tf, 1);
   double cl = iClose(_Symbol, g_setup_tf, 1);
   datetime bt = iTime(_Symbol, g_setup_tf, 1);

   ENUM_DIR d;
   if(!DetectStructureReversal(d, 1))
      return;

   g_dir         = d;
   g_sweep_time  = bt;      // prima candela forte opposta dopo HH1/LL1
   g_sweep_hi    = hi;
   g_sweep_lo    = lo;
   g_sw_body_top = MathMax(op, cl);
   g_sw_body_bot = MathMin(op, cl);
   if(d == D_SHORT)
   {
      g_protected_extreme = g_anchor_price; // HH1
   }
   else
   {
      g_protected_extreme = g_anchor_price; // LL1
   }

   g_liquidity_ok = false;
   g_liquidity_time = 0;
   g_liquidity_top = 0;
   g_liquidity_bot = 0;
   if(g_use_liquidity_zones)
   {
      g_liquidity_ok = FindLiquidityZoneAtAnchor(d, g_anchor_price,
                                                 g_anchor_time,
                                                 g_liquidity_src,
                                                 g_liquidity_time,
                                                 g_liquidity_top,
                                                 g_liquidity_bot);
      if(!g_liquidity_ok && g_require_liquidity_zone)
      {
         g_last_setup_anchor_time = g_anchor_time;
         Print("Setup scartato: HH1/LL1 senza sweep di FVG/OB");
         ResetState();
         return;
      }
   }

   g_last_setup_anchor_time = g_anchor_time;
   g_bos_time = 0;
   g_state = S_BOS;
   PrintSweep();
   if(InpShowLines)
      DrawSweep();
}

//------------------------------------------------------------------
//  STATE: BOS - attende rottura struttura M5 anche dopo piu candele
//------------------------------------------------------------------
void DoBOS()
{
   double hi = iHigh(_Symbol,  g_setup_tf, 1);
   double lo = iLow(_Symbol,   g_setup_tf, 1);
   double cl = iClose(_Symbol, g_setup_tf, 1);
   datetime bt = iTime(_Symbol, g_setup_tf, 1);

   if(bt <= g_sweep_time)
      return;

   if(IsSweepInvalidated(hi, lo))
   {
      ResetState();
      return;
   }

   if(BarsSinceReversal(bt) > InpMaxBarsAfterReversal)
   {
      ResetState();
      return;
   }

   // Short: HH1 crea il massimo protetto.
   // Il BOS avviene quando il prezzo rompe l'ultimo swing low M5.
   if(g_dir == D_SHORT)
   {
      bool broke = InpBOSRequiresClose ? (cl < g_bos_level) : (lo < g_bos_level);
      if(broke)
      {
         g_bos_time = bt;
         if(!FindTriggerBodyBeforeAnchor(g_dir, g_anchor_time,
                                        g_trigger_top, g_trigger_bot, g_trigger_time))
         {
            Print("BTS non valida/trovata: setup short scartato");
            ResetState();
            return;
         }
         g_state = S_TRIG;
         Print("BOS SHORT M5 @", TimeToString(g_bos_time),
               " | level=", g_bos_level,
               " | trigger body=[", g_trigger_bot, "-", g_trigger_top, "]");
         if(InpShowLines)
            DrawBOS(g_bos_level);
         return;
      }
   }

   // Long: LL1 crea il minimo protetto.
   // Il BOS avviene quando il prezzo rompe l'ultimo swing high M5.
   if(g_dir == D_LONG)
   {
      bool broke = InpBOSRequiresClose ? (cl > g_bos_level) : (hi > g_bos_level);
      if(broke)
      {
         g_bos_time = bt;
         if(!FindTriggerBodyBeforeAnchor(g_dir, g_anchor_time,
                                        g_trigger_top, g_trigger_bot, g_trigger_time))
         {
            Print("STB non valida/trovata: setup long scartato");
            ResetState();
            return;
         }
         g_state = S_TRIG;
         Print("BOS LONG M5 @", TimeToString(g_bos_time),
               " | level=", g_bos_level,
               " | trigger body=[", g_trigger_bot, "-", g_trigger_top, "]");
         if(InpShowLines)
            DrawBOS(g_bos_level);
         return;
      }
   }
}

//------------------------------------------------------------------
//  STATE: TRIGGER su M5 - controlla invalidazione e timeout
//------------------------------------------------------------------
void DoTrigM5()
{
   double hi = iHigh(_Symbol, g_setup_tf, 1);
   double lo = iLow(_Symbol,  g_setup_tf, 1);
   datetime bt = iTime(_Symbol, g_setup_tf, 1);

   if(IsSweepInvalidated(hi, lo))
   {
      ResetState();
      return;
   }

   if(BarsSinceReversal(bt) > InpMaxBarsAfterReversal)
      ResetState();
}

//------------------------------------------------------------------
//  NUOVA BARRA M1 - solo invalidazione pre-trigger
//------------------------------------------------------------------
void OnBarM1()
{
   if(g_state != S_TRIG)
      return;

   double hi1 = iHigh(_Symbol, g_trigger_tf, 1);
   double lo1 = iLow(_Symbol,  g_trigger_tf, 1);
   if(IsSweepInvalidated(hi1, lo1))
      ResetState();
}

//------------------------------------------------------------------
//  TRIGGER M1 - tick esatto oppure range M1 sul corpo BTS/STB
//------------------------------------------------------------------
void CheckTrigger()
{
   if(PositionExists())
      return;

   if(g_trigger_mode == TRIGGER_TICK_BODY)
      CheckTickBodyTrigger();
   else
      CheckM1RangeBodyTrigger();
}

void CheckTickBodyTrigger()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(bid <= 0 || ask <= 0)
      return;

   double tol = TriggerTolerancePrice();
   double top = g_trigger_top + tol;
   double bot = g_trigger_bot - tol;

   if(g_dir == D_SHORT)
   {
      if(ask > g_protected_extreme)
      {
         ResetState();
         return;
      }

      if(bid >= bot && bid <= top)
      {
         Print("TRIGGER SELL TICK: bid=", bid,
               " body=[", bot, "-", top, "]");
         ExecuteTrade();
      }
   }
   else
   {
      if(bid < g_protected_extreme)
      {
         ResetState();
         return;
      }

      if(ask >= bot && ask <= top)
      {
         Print("TRIGGER BUY TICK: ask=", ask,
               " body=[", bot, "-", top, "]");
         ExecuteTrade();
      }
   }
}

void CheckM1RangeBodyTrigger()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(bid <= 0 || ask <= 0)
      return;

   double tol = TriggerTolerancePrice();
   double top = g_trigger_top + tol;
   double bot = g_trigger_bot - tol;
   double hi0 = iHigh(_Symbol, g_trigger_tf, 0);
   double lo0 = iLow(_Symbol,  g_trigger_tf, 0);
   if(hi0 <= 0 || lo0 <= 0)
      return;

   bool range_touched = (hi0 >= bot && lo0 <= top);


   if(g_dir == D_SHORT)
   {
      bool invalidated = (hi0 > g_protected_extreme || ask > g_protected_extreme);
      if(invalidated)
      {
         ResetState();
         return;
      }

      if(range_touched)
      {
         Print("TRIGGER SELL M1 RANGE: range=[", lo0, "-", hi0,
               "] body=[", bot, "-", top, "]");
         ExecuteTrade();
      }
   }
   else
   {
      bool invalidated = (lo0 < g_protected_extreme || bid < g_protected_extreme);
      if(invalidated)
      {
         ResetState();
         return;
      }

      if(range_touched)
      {
         Print("TRIGGER BUY M1 RANGE: range=[", lo0, "-", hi0,
               "] body=[", bot, "-", top, "]");
         ExecuteTrade();
      }
   }
}

//------------------------------------------------------------------
//  ESECUZIONE TRADE
//------------------------------------------------------------------
void ExecuteTrade()
{
   if(PositionExists())
      return;

   if(!IsSpreadAllowed())
      return;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double entry, sl, tp, sl_pts;
   double buffer = SizeToPrice(g_sl_buffer_limit, g_protected_extreme);

   if(g_dir == D_SHORT)
   {
      entry  = bid;
      sl     = g_protected_extreme + buffer;
      sl_pts = sl - entry;
      tp     = entry - sl_pts * g_tp3_r;
      if(sl <= entry || tp >= entry)
      {
         ResetState();
         return;
      }
   }
   else
   {
      entry  = ask;
      sl     = g_protected_extreme - buffer;
      sl_pts = entry - sl;
      tp     = entry + sl_pts * g_tp3_r;
      if(sl >= entry || tp <= entry)
      {
         ResetState();
         return;
      }
   }

   double sl_pts_sym = sl_pts / _Point;
   double sl_limit_value = SizeFromPrice(sl_pts, entry);
   if(sl_limit_value < g_min_sl_limit)
   {
      double forced_dist = SizeToPrice(g_min_sl_limit, entry);
      if(g_dir == D_SHORT)
      {
         sl     = entry + forced_dist;
         sl_pts = forced_dist;
         tp     = entry - sl_pts * g_tp3_r;
      }
      else
      {
         sl     = entry - forced_dist;
         sl_pts = forced_dist;
         tp     = entry + sl_pts * g_tp3_r;
      }
      sl_pts_sym = sl_pts / _Point;
      sl_limit_value = g_min_sl_limit;
      Print("SL forzato a distanza minima: ", g_min_sl_limit,
            (g_use_bps_sizing ? " bps" : " punti"),
            " | sl_pts=", sl_pts_sym);
   }

   if(g_max_sl_limit > 0.0 && sl_limit_value > g_max_sl_limit)
   {
      Print("SL fuori range: ", sl_limit_value,
            (g_use_bps_sizing ? " bps" : " punti"),
            " | max=", g_max_sl_limit,
            " | sl_pts=", sl_pts_sym);
      ResetState();
      return;
   }

   double lot = CalcLot(sl_pts);
   if(lot <= 0)
   {
      ResetState();
      return;
   }

   sl = NormalizeDouble(sl, _Digits);
   tp = NormalizeDouble(tp, _Digits);

   string comment = StringFormat("SMC_ETH %s | SL=%.1f%s | TP1=%.2fR %.0f%% | TP2=%.2fR %.0f%% | TP3=%.2fR",
                                 (g_dir == D_SHORT ? "SHORT" : "LONG"),
                                 sl_limit_value,
                                 (g_use_bps_sizing ? "bps" : "pt"),
                                 g_tp1_r,
                                 g_tp1_close_pct,
                                 g_tp2_r,
                                 g_tp2_close_pct,
                                 g_tp3_r);

   bool ok = false;
   if(g_dir == D_SHORT)
      ok = g_trade.Sell(lot, _Symbol, 0, sl, tp, comment);
   else
      ok = g_trade.Buy(lot, _Symbol, 0, sl, tp, comment);

   if(ok)
   {
      Print("TRADE: ", (g_dir == D_SHORT ? "SELL" : "BUY"),
            " entry=", entry, " sl=", sl, " tp=", tp,
            " lot=", lot, " sl_pts=", sl_pts_sym);
      if(InpAlerts)
         Alert("SMC ", (g_dir == D_SHORT ? "SHORT" : "LONG"),
               " | ", _Symbol, " | TP=", DoubleToString(tp, _Digits));
      if(InpShowLines)
         DrawTrade(entry, sl, tp);
   }
   else
   {
      Print("ERRORE TRADE: ", g_trade.ResultRetcode(),
            " - ", g_trade.ResultRetcodeDescription());
   }

   ResetState();
}

//------------------------------------------------------------------
//  ZONE DI LIQUIDITA - FVG H1 / OB M15
//------------------------------------------------------------------
void RebuildZones()
{
   g_nz = 0;
   ArrayResize(g_zones, 0);

   int h1bars = MathMin(InpFVG_H1_Bars, iBars(_Symbol, g_fvg_tf) - 2);
   for(int i = h1bars; i >= 2; i--)
   {
      double hi_p = iHigh(_Symbol, g_fvg_tf, i + 1);
      double lo_p = iLow(_Symbol,  g_fvg_tf, i + 1);
      double hi_n = iHigh(_Symbol, g_fvg_tf, i - 1);
      double lo_n = iLow(_Symbol,  g_fvg_tf, i - 1);
      datetime zt = iTime(_Symbol, g_fvg_tf, i);

      if(lo_n > hi_p)
         AddZone(zt, lo_n, hi_p, (lo_n + hi_p) / 2.0, LT_LOW, LS_FVG);
      if(hi_n < lo_p)
         AddZone(zt, lo_p, hi_n, (lo_p + hi_n) / 2.0, LT_HIGH, LS_FVG);
   }

   int m15bars = MathMin(InpOB_M15_Bars, iBars(_Symbol, g_ob_tf) - 2);
   for(int i = m15bars; i >= 2; i--)
   {
      double o0 = iOpen(_Symbol,  g_ob_tf, i);
      double c0 = iClose(_Symbol, g_ob_tf, i);
      double o1 = iOpen(_Symbol,  g_ob_tf, i - 1);
      double c1 = iClose(_Symbol, g_ob_tf, i - 1);
      double b0 = MathAbs(c0 - o0);
      double b1 = MathAbs(c1 - o1);
      datetime zt = iTime(_Symbol, g_ob_tf, i);

      if(c0 < o0 && c1 > o1 && b1 > b0 * InpOB_ImpMult)
         AddZone(zt, MathMax(o0, c0), MathMin(o0, c0), 0, LT_LOW, LS_OB);
      if(c0 > o0 && c1 < o1 && b1 > b0 * InpOB_ImpMult)
         AddZone(zt, MathMax(o0, c0), MathMin(o0, c0), 0, LT_HIGH, LS_OB);
   }
}

void AddZone(datetime t, double top, double bot, double mid,
             ENUM_LTYPE lt, ENUM_LSRC src)
{
   bool consumed = false;
   if(src == LS_OB)
      for(int i = 0; i < g_ob_consumed_n; i++)
         if(g_ob_consumed[i] == t)
         {
            consumed = true;
            break;
         }

   int idx = g_nz;
   ArrayResize(g_zones, g_nz + 1);
   g_zones[idx].time     = t;
   g_zones[idx].top      = top;
   g_zones[idx].bot      = bot;
   g_zones[idx].mid      = mid;
   g_zones[idx].ltype    = lt;
   g_zones[idx].src      = src;
   g_zones[idx].consumed = consumed;
   g_nz++;
}

void MarkOBConsumed(datetime t)
{
   for(int i = 0; i < g_ob_consumed_n; i++)
      if(g_ob_consumed[i] == t)
         return;

   ArrayResize(g_ob_consumed, g_ob_consumed_n + 1);
   g_ob_consumed[g_ob_consumed_n] = t;
   g_ob_consumed_n++;

   for(int i = 0; i < g_nz; i++)
      if(g_zones[i].src == LS_OB && g_zones[i].time == t)
         g_zones[i].consumed = true;
}

bool FindLiquidityZoneAtAnchor(ENUM_DIR d, double anchor_price,
                               datetime anchor_time,
                               ENUM_LSRC &src, datetime &zt,
                               double &top, double &bot)
{
   for(int pass = 0; pass < 2; pass++)
   {
      ENUM_LSRC wanted = (pass == 0) ? LS_FVG : LS_OB;
      for(int i = g_nz - 1; i >= 0; i--)
      {
         Zone z = g_zones[i];
         if(z.src != wanted) continue;
         if(z.consumed)   continue;
         if(z.time >= anchor_time) continue;

         if(d == D_SHORT && z.ltype == LT_HIGH && IsAnchorBeyondZone(z, anchor_price))
         {
            src = z.src;
            zt = z.time;
            top = z.top;
            bot = z.bot;
            if(z.src == LS_OB)
               MarkOBConsumed(z.time);
            return true;
         }

         if(d == D_LONG && z.ltype == LT_LOW && IsAnchorBeyondZone(z, anchor_price))
         {
            src = z.src;
            zt = z.time;
            top = z.top;
            bot = z.bot;
            if(z.src == LS_OB)
               MarkOBConsumed(z.time);
            return true;
         }
      }
   }

   return false;
}

bool IsAnchorBeyondZone(const Zone &z, double anchor_price)
{
   if(z.src == LS_FVG)
   {
      if(z.ltype == LT_HIGH)
         return anchor_price >= z.mid;
      return anchor_price <= z.mid;
   }

   if(z.ltype == LT_HIGH)
      return anchor_price >= z.bot;
   return anchor_price <= z.top;
}

//------------------------------------------------------------------
//  PRESET / UNITA DI MISURA
//------------------------------------------------------------------
void ConfigurePreset()
{
   g_active_preset = DetectPreset();

   g_risk_pct = InpRiskPct;
   g_max_spread_pts = InpMaxSpreadPts;
   g_max_spread_bps = InpMaxSpreadBps;
   g_min_sl_limit = InpMinSLPts;
   g_max_sl_limit = InpMaxSLPts;
   g_sl_buffer_limit = InpSLBufferPts;
   g_tp1_r = InpTP1_R;
   g_tp1_close_pct = InpTP1_ClosePct;
   g_tp2_r = InpTP2_R;
   g_tp2_close_pct = InpTP2_ClosePct;
   g_tp3_r = InpTP3_R;
   g_take_profit_r = InpTP3_R;
   g_be_trigger_r = InpBE_Trigger_R;
   g_use_partial_close = InpUsePartialClose;
   g_use_break_even = InpUseBreakEven;
   g_setup_tf = InpSetupTF;
   g_trigger_tf = InpTriggerTF;
   g_fvg_tf = InpFvgTF;
   g_ob_tf = InpObTF;
   g_min_reversal_body_limit = InpMinReversalBodyPts;
   g_min_bts_body_limit = InpMinBTSBodyPts;
   g_trigger_mode = InpTriggerMode;
   g_trigger_tolerance = InpTriggerTolerance;
   g_use_bps_sizing = InpUseBpsSizing;
   g_use_liquidity_zones = InpUseLiquidityZones;
   g_require_liquidity_zone = InpRequireLiquidityZone;

   if(g_active_preset == PRESET_GOLD)
   {
      g_use_bps_sizing = false;
      g_max_spread_pts = 30.0;
      g_min_sl_limit = 3.0;
      g_max_sl_limit = 1000.0;
      g_sl_buffer_limit = 30.0;
      g_take_profit_r = 0.5;
      g_min_reversal_body_limit = 10.0;
      g_min_bts_body_limit = 5.0;
      g_use_liquidity_zones = true;
      g_require_liquidity_zone = true;
   }
   else if(g_active_preset == PRESET_BTC)
   {
      g_use_bps_sizing = true;
      g_max_spread_bps = 5.0;
      g_min_sl_limit = 1.0;
      g_max_sl_limit = 150.0;
      g_sl_buffer_limit = 2.0;
      g_take_profit_r = 0.75;
      g_min_reversal_body_limit = 1.0;
      g_min_bts_body_limit = 0.0;
      g_use_liquidity_zones = true;
      g_require_liquidity_zone = true;
   }
   else if(g_active_preset == PRESET_ETH)
   {
      g_use_bps_sizing = true;
      g_max_spread_bps = 5.0;
      g_min_sl_limit = 200.0;
      g_max_sl_limit = 500.0;
      g_sl_buffer_limit = 1.0;
      g_tp1_r = 0.80;
      g_tp1_close_pct = 50.0;
      g_tp2_r = 1.20;
      g_tp2_close_pct = 30.0;
      g_tp3_r = 2.00;
      g_take_profit_r = g_tp3_r;
      g_be_trigger_r = 0.80;
      g_use_partial_close = true;
      g_use_break_even = true;
      g_min_reversal_body_limit = 5.0;
      g_min_bts_body_limit = 0.0;
      g_setup_tf = PERIOD_H1;
      g_trigger_tf = PERIOD_M5;
      g_fvg_tf = PERIOD_H4;
      g_ob_tf = PERIOD_H1;
      g_use_liquidity_zones = true;
      g_require_liquidity_zone = true;
   }
}

ENUM_PRESET_MODE DetectPreset()
{
   if(InpPresetMode != PRESET_AUTO)
      return InpPresetMode;

   string sym = _Symbol;
   if(StringFind(sym, "BTC") >= 0 || StringFind(sym, "btc") >= 0)
      return PRESET_BTC;
   if(StringFind(sym, "ETH") >= 0 || StringFind(sym, "eth") >= 0)
      return PRESET_ETH;
   if(StringFind(sym, "XAU") >= 0 || StringFind(sym, "xau") >= 0 ||
      StringFind(sym, "GOLD") >= 0 || StringFind(sym, "Gold") >= 0 ||
      StringFind(sym, "gold") >= 0)
      return PRESET_GOLD;

   return PRESET_CUSTOM;
}

string PresetName(ENUM_PRESET_MODE preset)
{
   switch(preset)
   {
      case PRESET_AUTO:   return "AUTO";
      case PRESET_CUSTOM: return "CUSTOM";
      case PRESET_GOLD:   return "GOLD";
      case PRESET_BTC:    return "BTC";
      case PRESET_ETH:    return "ETH";
   }
   return "UNKNOWN";
}

string TriggerModeName(ENUM_TRIGGER_MODE mode)
{
   switch(mode)
   {
      case TRIGGER_TICK_BODY:     return "TICK_BODY";
      case TRIGGER_M1_RANGE_BODY: return "M1_RANGE_BODY";
   }
   return "UNKNOWN";
}

double SizeToPrice(double value, double reference_price)
{
   if(g_use_bps_sizing)
      return reference_price * value / 10000.0;
   return value * _Point;
}

double SizeFromPrice(double price_distance, double reference_price)
{
   if(g_use_bps_sizing)
   {
      if(reference_price <= 0.0)
         return DBL_MAX;
      return price_distance / reference_price * 10000.0;
   }
   return price_distance / _Point;
}

bool IsSpreadAllowed()
{
   if(g_use_bps_sizing)
   {
      if(g_max_spread_bps <= 0.0)
         return true;
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double mid = (bid + ask) / 2.0;
      if(mid <= 0.0)
         return false;
      return ((ask - bid) / mid * 10000.0) <= g_max_spread_bps;
   }

   if(g_max_spread_pts <= 0.0)
      return true;
   double spread_pts = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   return spread_pts <= g_max_spread_pts;
}

double TriggerTolerancePrice()
{
   double reference = (g_trigger_top + g_trigger_bot) / 2.0;
   if(reference <= 0.0)
      reference = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(reference <= 0.0)
      return 0.0;
   return SizeToPrice(g_trigger_tolerance, reference);
}

//------------------------------------------------------------------
//  HELPER STRATEGIA
//------------------------------------------------------------------
bool IsSweepInvalidated(double hi, double lo)
{
   if(g_dir == D_SHORT && hi > g_protected_extreme)
      return true;
   if(g_dir == D_LONG && lo < g_protected_extreme)
      return true;
   return false;
}

int BarsSinceReversal(datetime bt)
{
   if(g_sweep_time == 0 || bt <= g_sweep_time)
      return 0;
   return (int)((bt - g_sweep_time) / PeriodSeconds(g_setup_tf));
}

bool DetectStructureReversal(ENUM_DIR &d, int reversal_shift)
{
   if(IsStrongReversalCandle(D_SHORT, reversal_shift) &&
      FindLongTrendBreakSetup(reversal_shift))
   {
      d = D_SHORT;
      return true;
   }

   if(IsStrongReversalCandle(D_LONG, reversal_shift) &&
      FindShortTrendBreakSetup(reversal_shift))
   {
      d = D_LONG;
      return true;
   }

   return false;
}

bool FindLongTrendBreakSetup(int reversal_shift)
{
   int max_shift = reversal_shift + InpStructSearchBars;
   for(int hh_shift = reversal_shift + 1; hh_shift <= max_shift; hh_shift++)
   {
      datetime hh_time = iTime(_Symbol, g_setup_tf, hh_shift);
      if(hh_time == 0)
         break;
      if(hh_shift - reversal_shift > InpMaxBarsAfterSwing)
         break;
      if(!IsSwingHighM5(hh_shift))
         continue;
      if(hh_time == g_last_setup_anchor_time)
         continue;

      double hh = iHigh(_Symbol, g_setup_tf, hh_shift);
      double prev_hh;
      datetime prev_hh_time;
      if(!FindOlderSwingHigh(hh_shift + 1, max_shift, prev_hh, prev_hh_time))
         continue;
      if(hh <= prev_hh)
         continue;

      double hl;
      datetime hl_time;
      int hl_shift;
      if(!FindOlderSwingLowShift(hh_shift + 1, max_shift, hl_shift, hl, hl_time))
         continue;

      double prev_hl;
      datetime prev_hl_time;
      if(!FindOlderSwingLow(hl_shift + 1, max_shift, prev_hl, prev_hl_time))
         continue;
      if(hl <= prev_hl)
         continue;

      g_anchor_time = hh_time;
      g_anchor_price = hh;
      g_bos_level = hl;
      g_bos_level_time = hl_time;
      return true;
   }

   return false;
}

bool FindShortTrendBreakSetup(int reversal_shift)
{
   int max_shift = reversal_shift + InpStructSearchBars;
   for(int ll_shift = reversal_shift + 1; ll_shift <= max_shift; ll_shift++)
   {
      datetime ll_time = iTime(_Symbol, g_setup_tf, ll_shift);
      if(ll_time == 0)
         break;
      if(ll_shift - reversal_shift > InpMaxBarsAfterSwing)
         break;
      if(!IsSwingLowM5(ll_shift))
         continue;
      if(ll_time == g_last_setup_anchor_time)
         continue;

      double ll = iLow(_Symbol, g_setup_tf, ll_shift);
      double prev_ll;
      datetime prev_ll_time;
      if(!FindOlderSwingLow(ll_shift + 1, max_shift, prev_ll, prev_ll_time))
         continue;
      if(ll >= prev_ll)
         continue;

      double lh;
      datetime lh_time;
      int lh_shift;
      if(!FindOlderSwingHighShift(ll_shift + 1, max_shift, lh_shift, lh, lh_time))
         continue;

      double prev_lh;
      datetime prev_lh_time;
      if(!FindOlderSwingHigh(lh_shift + 1, max_shift, prev_lh, prev_lh_time))
         continue;
      if(lh >= prev_lh)
         continue;

      g_anchor_time = ll_time;
      g_anchor_price = ll;
      g_bos_level = lh;
      g_bos_level_time = lh_time;
      return true;
   }

   return false;
}

bool IsStrongReversalCandle(ENUM_DIR d, int shift)
{
   double o = iOpen(_Symbol, g_setup_tf, shift);
   double c = iClose(_Symbol, g_setup_tf, shift);
   double h = iHigh(_Symbol, g_setup_tf, shift);
   double l = iLow(_Symbol, g_setup_tf, shift);
   double body = MathAbs(c - o);
   double range = h - l;
   if(range <= 0)
      return false;
   if(d == D_SHORT && c >= o)
      return false;
   if(d == D_LONG && c <= o)
      return false;
   if(body < SizeToPrice(g_min_reversal_body_limit, c))
      return false;
   if(body / range < InpMinReversalBodyRatio)
      return false;

   double avg_body = AvgM5Body(shift + 1, InpReversalAvgBars);
   if(avg_body > 0 && body < avg_body * InpReversalAvgMult)
      return false;

   return true;
}

double AvgM5Body(int start_shift, int bars)
{
   double sum = 0.0;
   int n = 0;
   for(int sh = start_shift; sh < start_shift + bars; sh++)
   {
      datetime t = iTime(_Symbol, g_setup_tf, sh);
      if(t == 0)
         break;
      sum += MathAbs(iClose(_Symbol, g_setup_tf, sh) -
                     iOpen(_Symbol, g_setup_tf, sh));
      n++;
   }

   if(n == 0)
      return 0.0;
   return sum / n;
}

bool IsSwingLowM5(int shift)
{
   int left = MathMax(1, InpSwingLeftBars);
   int right = MathMax(1, InpSwingRightBars);
   double center = iLow(_Symbol, g_setup_tf, shift);
   if(center <= 0)
      return false;

   for(int i = 1; i <= left; i++)
   {
      double older = iLow(_Symbol, g_setup_tf, shift + i);
      if(older <= 0 || older <= center)
         return false;
   }

   for(int i = 1; i <= right; i++)
   {
      if(shift - i < 1)
         return false;
      double newer = iLow(_Symbol, g_setup_tf, shift - i);
      if(newer <= 0 || newer <= center)
         return false;
   }

   return true;
}

bool IsSwingHighM5(int shift)
{
   int left = MathMax(1, InpSwingLeftBars);
   int right = MathMax(1, InpSwingRightBars);
   double center = iHigh(_Symbol, g_setup_tf, shift);
   if(center <= 0)
      return false;

   for(int i = 1; i <= left; i++)
   {
      double older = iHigh(_Symbol, g_setup_tf, shift + i);
      if(older <= 0 || older >= center)
         return false;
   }

   for(int i = 1; i <= right; i++)
   {
      if(shift - i < 1)
         return false;
      double newer = iHigh(_Symbol, g_setup_tf, shift - i);
      if(newer <= 0 || newer >= center)
         return false;
   }

   return true;
}

bool FindOlderSwingLowShift(int start_shift, int max_shift,
                            int &found_shift, double &level, datetime &level_time)
{
   for(int sh = start_shift; sh <= max_shift; sh++)
   {
      datetime t = iTime(_Symbol, g_setup_tf, sh);
      if(t == 0)
         break;
      if(!IsSwingLowM5(sh))
         continue;
      found_shift = sh;
      level = iLow(_Symbol, g_setup_tf, sh);
      level_time = t;
      return true;
   }

   return false;
}

bool FindOlderSwingHighShift(int start_shift, int max_shift,
                             int &found_shift, double &level, datetime &level_time)
{
   for(int sh = start_shift; sh <= max_shift; sh++)
   {
      datetime t = iTime(_Symbol, g_setup_tf, sh);
      if(t == 0)
         break;
      if(!IsSwingHighM5(sh))
         continue;
      found_shift = sh;
      level = iHigh(_Symbol, g_setup_tf, sh);
      level_time = t;
      return true;
   }

   return false;
}

bool FindOlderSwingLow(int start_shift, int max_shift,
                       double &level, datetime &level_time)
{
   int found_shift;
   return FindOlderSwingLowShift(start_shift, max_shift,
                                 found_shift, level, level_time);
}

bool FindOlderSwingHigh(int start_shift, int max_shift,
                        double &level, datetime &level_time)
{
   int found_shift;
   return FindOlderSwingHighShift(start_shift, max_shift,
                                  found_shift, level, level_time);
}

bool FindTriggerBodyBeforeAnchor(ENUM_DIR d, datetime anchor_time,
                                double &top, double &bot, datetime &body_time)
{
   int anchor_shift = iBarShift(_Symbol, g_setup_tf, anchor_time, true);
   if(anchor_shift < 0)
      return false;

   int max_shift = anchor_shift + InpBTSSearchBars;
   return FindPriorDirectionalBody(anchor_shift + 1, max_shift,
                                   d == D_SHORT, top, bot, body_time);
}

bool FindPriorDirectionalBody(int start_shift, int max_shift, bool need_buy,
                              double &top, double &bot, datetime &body_time)
{
   double min_body = SizeToPrice(g_min_bts_body_limit, iClose(_Symbol, g_setup_tf, start_shift));
   for(int sh = start_shift; sh <= max_shift; sh++)
   {
      datetime t = iTime(_Symbol, g_setup_tf, sh);
      if(t == 0)
         break;

      double o = iOpen(_Symbol, g_setup_tf, sh);
      double c = iClose(_Symbol, g_setup_tf, sh);
      double h = iHigh(_Symbol, g_setup_tf, sh);
      double l = iLow(_Symbol, g_setup_tf, sh);
      bool is_buy = (c > o);
      bool is_sell = (c < o);
      if(need_buy && !is_buy)
         continue;
      if(!need_buy && !is_sell)
         continue;

      double body = MathAbs(c - o);
      double range = h - l;
      if(range > 0 && body >= min_body && body / range >= InpMinBTSBodyRatio)
      {
         top = MathMax(o, c);
         bot = MathMin(o, c);
         body_time = t;
         return true;
      }

      // Se l'ultima BTS/STB e' troppo piatta, usa il blocco formato dalle
      // ultime due candele direzionali valide prima dell'anchor.
      double pair_top, pair_bot;
      datetime pair_time;
      if(FindTwoCandleBody(sh, max_shift, need_buy,
                           pair_top, pair_bot, pair_time))
      {
         top = pair_top;
         bot = pair_bot;
         body_time = pair_time;
         return true;
      }
   }

   return false;
}

bool FindTwoCandleBody(int first_shift, int max_shift, bool need_buy,
                       double &top, double &bot, datetime &body_time)
{
   int second_shift = -1;
   for(int sh = first_shift + 1; sh <= max_shift; sh++)
   {
      datetime t = iTime(_Symbol, g_setup_tf, sh);
      if(t == 0)
         break;
      double o = iOpen(_Symbol, g_setup_tf, sh);
      double c = iClose(_Symbol, g_setup_tf, sh);
      if(need_buy && c > o)
      {
         second_shift = sh;
         break;
      }
      if(!need_buy && c < o)
      {
         second_shift = sh;
         break;
      }
   }

   if(second_shift < 0)
      return false;

   double o1 = iOpen(_Symbol, g_setup_tf, first_shift);
   double c1 = iClose(_Symbol, g_setup_tf, first_shift);
   double h1 = iHigh(_Symbol, g_setup_tf, first_shift);
   double l1 = iLow(_Symbol, g_setup_tf, first_shift);
   double o2 = iOpen(_Symbol, g_setup_tf, second_shift);
   double c2 = iClose(_Symbol, g_setup_tf, second_shift);
   double h2 = iHigh(_Symbol, g_setup_tf, second_shift);
   double l2 = iLow(_Symbol, g_setup_tf, second_shift);

   top = MathMax(MathMax(o1, c1), MathMax(o2, c2));
   bot = MathMin(MathMin(o1, c1), MathMin(o2, c2));
   double body = top - bot;
   double range = MathMax(h1, h2) - MathMin(l1, l2);
   if(range <= 0 || body < SizeToPrice(g_min_bts_body_limit, (top + bot) / 2.0) ||
      body / range < InpMinBTSBodyRatio)
      return false;

   body_time = iTime(_Symbol, g_setup_tf, first_shift);
   return true;
}


string PositionFlagName(ulong ticket, string suffix)
{
   return "SMC_ETH_" + suffix + "_" + IntegerToString((long)ticket);
}

bool PositionFlagIsSet(ulong ticket, string suffix)
{
   return GlobalVariableCheck(PositionFlagName(ticket, suffix));
}

void PositionFlagSet(ulong ticket, string suffix)
{
   GlobalVariableSet(PositionFlagName(ticket, suffix), (double)TimeCurrent());
}

double PositionInitialVolume(ulong ticket, double current_volume)
{
   string name = PositionFlagName(ticket, "INITVOL");
   if(!GlobalVariableCheck(name))
      GlobalVariableSet(name, current_volume);
   return GlobalVariableGet(name);
}

double NormalizeVolumeForSymbol(double volume)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0)
      return 0.0;
   double out = MathFloor(volume / step) * step;
   if(out < vmin)
      return 0.0;
   if(out > vmax)
      out = vmax;
   return NormalizeDouble(out, 2);
}

void ManageOpenPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket))
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol ||
         PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      ManagePosition(ticket);
   }
}

void TryPartialClose(ulong ticket, string flag, double initial_volume, double close_pct, double current_r)
{
   if(close_pct <= 0.0 || PositionFlagIsSet(ticket, flag))
      return;
   if(!PositionSelectByTicket(ticket))
      return;
   double vol = PositionGetDouble(POSITION_VOLUME);
   double close_volume = NormalizeVolumeForSymbol(initial_volume * close_pct / 100.0);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   if(close_volume > 0.0 && vol - close_volume < vmin)
      close_volume = NormalizeVolumeForSymbol(vol - vmin);

   if(close_volume > 0.0 && close_volume < vol)
   {
      if(g_trade.PositionClosePartial(ticket, close_volume))
      {
         PositionFlagSet(ticket, flag);
         Print(flag, " parziale eseguito | ticket=", ticket,
               " | volume=", close_volume,
               " | R=", DoubleToString(current_r, 2));
      }
      else
      {
         Print(flag, " partial close errore | ticket=", ticket,
               " | retcode=", g_trade.ResultRetcode(),
               " | ", g_trade.ResultRetcodeDescription());
      }
   }
   else
   {
      PositionFlagSet(ticket, flag);
      Print(flag, " parziale saltato: volume non compatibile con minimo broker | ticket=", ticket);
   }
}

void ManagePosition(ulong ticket)
{
   if(!PositionSelectByTicket(ticket))
      return;

   long   ptype = PositionGetInteger(POSITION_TYPE);
   double entry = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl    = PositionGetDouble(POSITION_SL);
   double tp    = PositionGetDouble(POSITION_TP);
   double vol   = PositionGetDouble(POSITION_VOLUME);
   if(entry <= 0.0 || tp <= 0.0 || vol <= 0.0 || g_tp3_r <= 0.0)
      return;

   double initial_volume = PositionInitialVolume(ticket, vol);
   double risk_dist = MathAbs(tp - entry) / g_tp3_r;
   if(risk_dist <= 0.0)
      return;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double current_r = 0.0;
   if(ptype == POSITION_TYPE_BUY)
      current_r = (bid - entry) / risk_dist;
   else if(ptype == POSITION_TYPE_SELL)
      current_r = (entry - ask) / risk_dist;
   else
      return;

   if(g_use_partial_close && current_r >= g_tp1_r)
      TryPartialClose(ticket, "TP1", initial_volume, g_tp1_close_pct, current_r);

   if(g_use_break_even && !PositionFlagIsSet(ticket, "BE") && current_r >= g_be_trigger_r)
   {
      double new_sl = NormalizeDouble(entry, _Digits);
      bool should_modify = false;
      if(ptype == POSITION_TYPE_BUY)
         should_modify = (sl <= 0.0 || sl < new_sl);
      else
         should_modify = (sl <= 0.0 || sl > new_sl);

      if(should_modify)
      {
         if(g_trade.PositionModify(ticket, new_sl, tp))
         {
            PositionFlagSet(ticket, "BE");
            Print("Break-even impostato | ticket=", ticket,
                  " | SL=", DoubleToString(new_sl, _Digits));
         }
         else
         {
            Print("Break-even errore | ticket=", ticket,
                  " | retcode=", g_trade.ResultRetcode(),
                  " | ", g_trade.ResultRetcodeDescription());
         }
      }
      else
      {
         PositionFlagSet(ticket, "BE");
      }
   }

   if(g_use_partial_close && current_r >= g_tp2_r)
      TryPartialClose(ticket, "TP2", initial_volume, g_tp2_close_pct, current_r);
}

double CalcLot(double sl_pts)
{
   double bal   = AccountInfoDouble(ACCOUNT_BALANCE);
   double risk  = bal * g_risk_pct / 100.0;
   double tsz   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tv    = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tsz <= 0 || tv <= 0 || sl_pts <= 0)
      return 0;

   double lot   = risk / (sl_pts / tsz * tv);
   double lstep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double lmin  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double lmax  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   lot = MathFloor(lot / lstep) * lstep;
   return NormalizeDouble(MathMax(lmin, MathMin(lmax, lot)), 2);
}

bool PositionExists()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(PositionSelectByTicket(t))
         if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
            PositionGetInteger(POSITION_MAGIC) == InpMagic)
            return true;
   }
   return false;
}

bool IsInSession()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return (dt.hour >= InpSessStart && dt.hour < InpSessEnd);
}

void ResetState()
{
   g_state             = S_SEEK;
   g_sweep_time        = 0;
   g_bos_time          = 0;
   g_sweep_hi          = 0;
   g_sweep_lo          = 0;
   g_sw_body_top       = 0;
   g_sw_body_bot       = 0;
   g_protected_extreme = 0;
   g_bos_level         = 0;
   g_bos_level_time    = 0;
   g_anchor_time       = 0;
   g_anchor_price      = 0;
   g_liquidity_ok      = false;
   g_liquidity_time    = 0;
   g_liquidity_top     = 0;
   g_liquidity_bot     = 0;
   g_trigger_top       = 0;
   g_trigger_bot       = 0;
   g_trigger_time      = 0;
}

void PrintSweep()
{
   Print("STRUCT REVERSAL ", (g_dir == D_SHORT ? "SHORT" : "LONG"),
         " | anchor_time=", TimeToString(g_anchor_time),
         " | anchor_price=", g_anchor_price,
         " | reversal_time=", TimeToString(g_sweep_time),
         " | reversal_hi=", g_sweep_hi,
         " | reversal_lo=", g_sweep_lo,
         " | bos_level=", g_bos_level,
         " | bos_level_time=", TimeToString(g_bos_level_time),
         " | protected=", g_protected_extreme,
         " | liquidity=", (g_liquidity_ok ?
            (g_liquidity_src == LS_FVG ? "FVG" : "OB") : "none"));
}

//------------------------------------------------------------------
//  DRAW
//------------------------------------------------------------------
void DrawSweep()
{
   ObjectsDeleteAll(0, "SMC_SW");
   color c = (g_dir == D_SHORT) ? clrOrangeRed : clrDodgerBlue;

   CreateHLine("SMC_SW_BODY_TOP", g_sw_body_top, c, STYLE_DASH, 1);
   CreateHLine("SMC_SW_BODY_BOT", g_sw_body_bot, c, STYLE_DASH, 1);
   CreateHLine("SMC_SW_PROTECTED", g_protected_extreme, clrRed, STYLE_DOT, 1);
   CreateHLine("SMC_SW_BOS_LEVEL", g_bos_level, clrYellow, STYLE_DOT, 1);
   if(g_liquidity_ok)
   {
      CreateHLine("SMC_LIQ_TOP", g_liquidity_top, clrViolet, STYLE_DOT, 1);
      CreateHLine("SMC_LIQ_BOT", g_liquidity_bot, clrViolet, STYLE_DOT, 1);
   }
}

void DrawBOS(double lvl)
{
   ObjectsDeleteAll(0, "SMC_BOS");
   CreateHLine("SMC_BOS", lvl, clrYellow, STYLE_DASH, 2);
   CreateHLine("SMC_TRIGGER_TOP", g_trigger_top, clrAqua, STYLE_DASH, 1);
   CreateHLine("SMC_TRIGGER_BOT", g_trigger_bot, clrAqua, STYLE_DASH, 1);
}

void DrawTrade(double entry, double sl, double tp)
{
   ObjectsDeleteAll(0, "SMC_TR");
   color ce = (g_dir == D_SHORT) ? clrTomato : clrLimeGreen;
   CreateHLine("SMC_TR_EN", entry, ce, STYLE_SOLID, 2);
   CreateHLine("SMC_TR_SL", sl, clrRed, STYLE_SOLID, 1);
   CreateHLine("SMC_TR_TP", tp, clrLime, STYLE_SOLID, 1);
}

void CreateHLine(string name, double price, color clr, int style, int width)
{
   if(ObjectFind(0, name) >= 0)
      ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
}
//+------------------------------------------------------------------+