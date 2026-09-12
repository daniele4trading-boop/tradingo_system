//+------------------------------------------------------------------+
//|  SpaghettiForex_EA.mq5                                           |
//|  Expert Advisor unificato — 6 strategie + Hedge Engine           |
//|  v2.01 — MTF2 pip fix, default prudente senza MTF2           |
//+------------------------------------------------------------------+
//
//  STRATEGIE:
//  SCALP1 — Stoch RSI + MACD confluenza
//  SCALP2 — EMA cross + DMI
//  SCALP3 — Divergenze hidden/regular (OFF di default, rumoroso su M1)
//  MTF1   — 3x Stoch RSI M1/M2/M3 (Ammazza Stop Loss) — ora nativo
//  MTF2   — Divergenze d'Oro (Momentum + hidden divergence)
//  SMC1   — Inefficienze d'Oro (pattern 3 candele M15)
//
//  LAYER TRASVERSALI:
//  Filtro trend H1 (EMA50 slope) · Session filter · MTF bias M15
//  Signal aggregator · Order manager · Hedge engine · Circuit breaker
//
//  STOCH RSI NATIVO:
//  L'EA usa iCustom() per caricare l'indicatore "Stochastic RSI"
//  disponibile gratuitamente su MQL5 Market (autore: MetaQuotes o
//  equivalente). Scarica l'indicatore, compilalo e mettilo in
//  MQL5/Indicators/ prima di compilare questo EA.
//  Nome file atteso: "Stochastic_RSI"  (parametri: RSI_Period,
//  Stoch_Period, K_Period, D_Period — tutti interi).
//  Se l'indicatore non è presente, l'EA fa fallback su iStochastic
//  standard con un avviso nel journal.
//+------------------------------------------------------------------+

#property copyright "SpaghettiForex EA v2.00"
#property version   "2.01"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

//+------------------------------------------------------------------+
//  SEZIONE 1 — ENUM
//+------------------------------------------------------------------+

enum ENUM_STRATEGY_MODE
{
   MODE_SCALP1 = 1,    // SCALP1: Stoch RSI + MACD confluenza
   MODE_SCALP2 = 2,    // SCALP2: EMA cross + DMI
   MODE_SCALP3 = 4,    // SCALP3: Divergenze Stoch RSI (OFF default)
   MODE_MTF1   = 8,    // MTF1: 3x Stoch RSI multi-TF
   MODE_MTF2   = 16,   // MTF2: Divergenze d'Oro
   MODE_SMC1   = 32,   // SMC1: Inefficienze d'Oro
   MODE_ALL    = 63    // Tutte le strategie
};

enum ENUM_LOT_QUALITY
{
   LOT_FULL      = 0,  // Rischio pieno
   LOT_TWO_THIRD = 1,  // 2/3 rischio
   LOT_ONE_THIRD = 2   // 1/3 rischio
};

enum ENUM_SIGNAL_DIR
{
   SIG_NONE = 0,
   SIG_BUY  = 1,
   SIG_SELL = -1
};

//+------------------------------------------------------------------+
//  SEZIONE 2 — INPUT
//+------------------------------------------------------------------+

// ── Strategie ──
input int    InpStrategyMask    = 11;    // Default prudente: 1+2+8 = SCALP1+SCALP2+MTF1 (MTF2 disattivato)
input bool   InpEnableHedge     = true;  // Abilita Hedge Engine anti-DD

// ── Rischio ──
input double InpRiskPerPipPct   = 0.10; // % capitale per pip (lottaggio base)
input double InpMaxDDPct        = 2.0;  // DD% giornaliero → circuit breaker
input double InpHedgeTriggerPct = 0.80; // DD% che attiva prima copertura
input int    InpMaxOpenTrades   = 2;    // Massimo posizioni totali aperte contemporaneamente
input int    InpMaxTradesPerSig = 1;    // Massimo posizioni per strategia
input int    InpMaxDailyTrades  = 6;    // Massimo trade totali al giorno (0 = illimitato)

// ── Sizing ──
input double InpLotMin          = 0.01;
input double InpLotMax          = 2.00;
input int    InpSlippage        = 3;    // Punti slippage

// ── Sessione ──
input bool   InpFilterSession   = true;
input int    InpSessionStartH   = 8;
input int    InpSessionEndH     = 21;

// ── EMA ──
input int    InpEMA_Fast        = 25;
input int    InpEMA_Mid         = 50;
input int    InpEMA_Slow        = 100;

// ── Stoch RSI (tutti i TF) ──
input int    InpSRSI_RSIPeriod  = 14;   // Periodo RSI interno
input int    InpSRSI_StochPer   = 14;   // Periodo Stocastico sul RSI
input int    InpSRSI_K          = 3;    // Smoothing K
input int    InpSRSI_D          = 3;    // Smoothing D
input double InpStochOB         = 80.0; // Soglia ipercomprato
input double InpStochOS         = 20.0; // Soglia ipervenduto
// Soglie MTF1 (più stringenti per segnale di qualità alta)
input double InpMTF1_OB         = 85.0; // OB per MTF1
input double InpMTF1_OS         = 15.0; // OS per MTF1

// ── MACD 5/13/8 ──
input int    InpMACD_Fast       = 5;
input int    InpMACD_Slow       = 13;
input int    InpMACD_Signal     = 8;
input double InpMACDThreshold   = 0.00007; // Soglia OB/OS MACD (EURUSD M1 default)

// ── Filtro trend H1 ──
input bool   InpUseH1Filter     = true;  // Abilita filtro trend H1
input int    InpH1_EMAPeriod    = 50;    // EMA usata per slope H1
input int    InpH1_SlopeBars    = 3;     // Barre H1 per calcolo slope

// ── Gestione posizioni ──
input bool   InpMoveToBE        = true;
input int    InpBEBuffer        = 2;     // Punti buffer BE
input double InpPartialPct1     = 50.0; // % chiusura al TP1
input double InpPartialPctHedge = 10.0; // % chiusura ciclo hedge
input double InpTP_DefaultPips  = 20.0; // TP default se non calcolato
input double InpSL_DefaultPips  = 12.0; // SL default

// ── Magic numbers ──
input int    InpMagicSCALP1     = 20260201;
input int    InpMagicSCALP2     = 20260202;
input int    InpMagicSCALP3     = 20260203;
input int    InpMagicMTF1       = 20260204;
input int    InpMagicMTF2       = 20260205;
input int    InpMagicSMC1       = 20260206;
input int    InpMagicHEDGE      = 20260299;

// ── Log ──
input bool   InpVerboseLog      = false;
input bool   InpReverseSignals  = true;  // REVERSE: inverte BUY->SELL e SELL->BUY
input bool   InpMTF2UseFixedPipUnits = true; // Fix v2.01: TP/SL MTF2 in punti corretti

//+------------------------------------------------------------------+
//  SEZIONE 3 — STRUTTURE
//+------------------------------------------------------------------+

struct SSignal
{
   ENUM_SIGNAL_DIR  direction;
   ENUM_LOT_QUALITY quality;
   int              strategyMask;
   double           suggestedTP;   // punti, 0=default
   double           suggestedSL;   // punti, 0=default
   string           comment;
};

struct SIndicatorCache
{
   // EMA M1
   double ema_fast, ema_mid, ema_slow;
   // EMA M15 (bias)
   double ema_fast_m15, ema_mid_m15, ema_slow_m15;
   // EMA H1 (filtro trend)
   double ema_h1_cur, ema_h1_prev;   // EMA50 H1 corrente e N barre fa

   // Stoch RSI M1 (nativo)
   double srsi_k,      srsi_d;       // correnti
   double srsi_k_prev, srsi_d_prev;  // barra precedente
   // Stoch RSI M2 (nativo)
   double srsi_k_m2, srsi_d_m2;
   // Stoch RSI M3 (nativo)
   double srsi_k_m3, srsi_d_m3;

   // MACD M1
   double macd_main, macd_signal;
   double macd_main_prev, macd_signal_prev;

   // DMI M1
   double dmi_plus, dmi_minus, dmi_adx;

   // Prezzi M1 (serie=true → [0]=corrente)
   double close[], high[], low[], open[];
   // Prezzi M15
   double close_m15[], high_m15[], low_m15[];

   // Trend H1: +1 rialzo, -1 ribasso, 0 neutro
   int    trend_h1;

   bool valid;
};

//+------------------------------------------------------------------+
//  SEZIONE 4 — VARIABILI GLOBALI
//+------------------------------------------------------------------+

CTrade          g_trade;
SIndicatorCache g_cache;

// Handle indicatori
int h_ema_fast_m1, h_ema_mid_m1, h_ema_slow_m1;
int h_ema_fast_m15, h_ema_mid_m15, h_ema_slow_m15;
int h_ema_h1;           // EMA H1 per filtro trend

// Stoch RSI — prova nativo, fallback su iStochastic
int h_srsi_m1;          // M1 nativo
int h_srsi_m2;          // M2 nativo
int h_srsi_m3;          // M3 nativo
bool g_srsi_native;     // true = indicatore nativo trovato

int h_macd_m1;
int h_dmi_m1;

// Tracking giornaliero
double   g_dayStartBalance  = 0.0;
double   g_maxDDReached     = 0.0;
bool     g_circuitBreakerOn = false;
datetime g_lastDayReset     = 0;

// Cooldown per strategia (una signal per candela M1)
datetime g_lastSignalTime[6];

// Cooldown GLOBALE: una sola apertura per candela M1 per pair
// Blocca le aperture multiple in rapida sequenza indipendentemente dalla strategia
datetime g_lastTradeBar = 0;

// Contatore trade giornalieri
int g_dailyTradeCount = 0;

//+------------------------------------------------------------------+
//  SEZIONE 5 — INIT / DEINIT
//+------------------------------------------------------------------+

int OnInit()
{
   g_trade.SetDeviationInPoints(InpSlippage);
   g_trade.SetTypeFilling(ORDER_FILLING_IOC);

   // EMA M1
   h_ema_fast_m1 = iMA(_Symbol, PERIOD_M1, InpEMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   h_ema_mid_m1  = iMA(_Symbol, PERIOD_M1, InpEMA_Mid,  0, MODE_EMA, PRICE_CLOSE);
   h_ema_slow_m1 = iMA(_Symbol, PERIOD_M1, InpEMA_Slow, 0, MODE_EMA, PRICE_CLOSE);

   // EMA M15 (bias direzionale)
   h_ema_fast_m15 = iMA(_Symbol, PERIOD_M15, InpEMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   h_ema_mid_m15  = iMA(_Symbol, PERIOD_M15, InpEMA_Mid,  0, MODE_EMA, PRICE_CLOSE);
   h_ema_slow_m15 = iMA(_Symbol, PERIOD_M15, InpEMA_Slow, 0, MODE_EMA, PRICE_CLOSE);

   // EMA H1 per filtro trend
   h_ema_h1 = iMA(_Symbol, PERIOD_H1, InpH1_EMAPeriod, 0, MODE_EMA, PRICE_CLOSE);

   // MACD M1
   h_macd_m1 = iMACD(_Symbol, PERIOD_M1, InpMACD_Fast, InpMACD_Slow, InpMACD_Signal, PRICE_CLOSE);

   // DMI / ADX M1
   h_dmi_m1 = iADX(_Symbol, PERIOD_M1, 14);

   // ── Stoch RSI nativo tramite iCustom ──
   // Prova a caricare l'indicatore custom "Stochastic_RSI".
   // Parametri standard: RSI_Period, Stoch_Period, K_Period, D_Period.
   // Buffer 0 = linea K, Buffer 1 = linea D.
   g_srsi_native = false;

   h_srsi_m1 = iCustom(_Symbol, PERIOD_M1, "Stochastic_RSI",
                        InpSRSI_RSIPeriod, InpSRSI_StochPer, InpSRSI_K, InpSRSI_D);
   if(h_srsi_m1 != INVALID_HANDLE)
   {
      h_srsi_m2 = iCustom(_Symbol, PERIOD_M2, "Stochastic_RSI",
                           InpSRSI_RSIPeriod, InpSRSI_StochPer, InpSRSI_K, InpSRSI_D);
      h_srsi_m3 = iCustom(_Symbol, PERIOD_M3, "Stochastic_RSI",
                           InpSRSI_RSIPeriod, InpSRSI_StochPer, InpSRSI_K, InpSRSI_D);

      if(h_srsi_m2 != INVALID_HANDLE && h_srsi_m3 != INVALID_HANDLE)
      {
         g_srsi_native = true;
         Print("SpaghettiEA v2.00: Stoch RSI NATIVO caricato su M1/M2/M3.");
      }
      else
      {
         // M2/M3 falliti — fallback totale
         if(h_srsi_m1 != INVALID_HANDLE) IndicatorRelease(h_srsi_m1);
         if(h_srsi_m2 != INVALID_HANDLE) IndicatorRelease(h_srsi_m2);
      }
   }

   if(!g_srsi_native)
   {
      // FALLBACK: iStochastic standard (proxy — risultati meno precisi)
      Print("SpaghettiEA v2.00: ATTENZIONE — 'Stochastic_RSI' non trovato in Indicators/.");
      Print("  Usa fallback iStochastic standard. WR MTF1 sarà inferiore.");
      Print("  Scarica 'Stochastic RSI' da MQL5 Market e ricompila l'EA.");

      h_srsi_m1 = iStochastic(_Symbol, PERIOD_M1,
                               InpSRSI_StochPer, InpSRSI_K, InpSRSI_D, MODE_SMA, STO_LOWHIGH);
      h_srsi_m2 = iStochastic(_Symbol, PERIOD_M2,
                               InpSRSI_StochPer, InpSRSI_K, InpSRSI_D, MODE_SMA, STO_LOWHIGH);
      h_srsi_m3 = iStochastic(_Symbol, PERIOD_M3,
                               InpSRSI_StochPer, InpSRSI_K, InpSRSI_D, MODE_SMA, STO_LOWHIGH);
   }

   // Verifica handle obbligatori
   if(h_ema_fast_m1 == INVALID_HANDLE || h_macd_m1 == INVALID_HANDLE ||
      h_srsi_m1     == INVALID_HANDLE || h_dmi_m1  == INVALID_HANDLE ||
      h_ema_h1      == INVALID_HANDLE)
   {
      Print("SpaghettiEA: INIT FAILED — handle indicatori non validi.");
      return INIT_FAILED;
   }

   // Prepara array serie
   ArraySetAsSeries(g_cache.close,    true);
   ArraySetAsSeries(g_cache.high,     true);
   ArraySetAsSeries(g_cache.low,      true);
   ArraySetAsSeries(g_cache.open,     true);
   ArraySetAsSeries(g_cache.close_m15,true);
   ArraySetAsSeries(g_cache.high_m15, true);
   ArraySetAsSeries(g_cache.low_m15,  true);

   g_dayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   g_lastDayReset    = TimeCurrent();
   ArrayInitialize(g_lastSignalTime, 0);

   Print("SpaghettiForex EA v2.01 REVERSE_PURE fixed pronto. Mask=", InpStrategyMask,
         " H1filter=", InpUseH1Filter,
         " StochRSI=", (g_srsi_native ? "NATIVO" : "FALLBACK"));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   IndicatorRelease(h_ema_fast_m1);   IndicatorRelease(h_ema_mid_m1);
   IndicatorRelease(h_ema_slow_m1);   IndicatorRelease(h_ema_fast_m15);
   IndicatorRelease(h_ema_mid_m15);   IndicatorRelease(h_ema_slow_m15);
   IndicatorRelease(h_ema_h1);
   IndicatorRelease(h_srsi_m1);       IndicatorRelease(h_srsi_m2);
   IndicatorRelease(h_srsi_m3);       IndicatorRelease(h_macd_m1);
   IndicatorRelease(h_dmi_m1);
}

//+------------------------------------------------------------------+
//  SEZIONE 6 — ONTICK
//+------------------------------------------------------------------+

void OnTick()
{
   CheckDayReset();
   if(g_circuitBreakerOn) return;
   if(!UpdateCache())     return;
   if(!IsSessionAllowed()) return;

   if(InpEnableHedge) RunHedgeEngine();
   if(!SafetyCheck()) return;

   SSignal sig;
   sig.direction    = SIG_NONE;
   sig.quality      = LOT_ONE_THIRD;
   sig.strategyMask = 0;
   sig.suggestedTP  = 0;
   sig.suggestedSL  = 0;
   sig.comment      = "";

   if(GenerateSignal(sig) && sig.direction != SIG_NONE)
      ExecuteSignal(sig);

   ManageOpenPositions();
}

//+------------------------------------------------------------------+
//  SEZIONE 7 — AGGIORNAMENTO CACHE
//+------------------------------------------------------------------+

bool UpdateCache()
{
   g_cache.valid = false;

   // Prezzi M1
   if(CopyClose(_Symbol, PERIOD_M1,  0, 6, g_cache.close)  < 6) return false;
   if(CopyHigh (_Symbol, PERIOD_M1,  0, 6, g_cache.high)   < 6) return false;
   if(CopyLow  (_Symbol, PERIOD_M1,  0, 6, g_cache.low)    < 6) return false;
   if(CopyOpen (_Symbol, PERIOD_M1,  0, 6, g_cache.open)   < 6) return false;

   // Prezzi M15
   if(CopyClose(_Symbol, PERIOD_M15, 0, 6, g_cache.close_m15) < 6) return false;
   if(CopyHigh (_Symbol, PERIOD_M15, 0, 6, g_cache.high_m15)  < 6) return false;
   if(CopyLow  (_Symbol, PERIOD_M15, 0, 6, g_cache.low_m15)   < 6) return false;

   double buf[];
   ArraySetAsSeries(buf, true);

   // EMA M1
   if(CopyBuffer(h_ema_fast_m1, 0, 0, 2, buf) < 2) return false; g_cache.ema_fast = buf[0];
   if(CopyBuffer(h_ema_mid_m1,  0, 0, 2, buf) < 2) return false; g_cache.ema_mid  = buf[0];
   if(CopyBuffer(h_ema_slow_m1, 0, 0, 2, buf) < 2) return false; g_cache.ema_slow = buf[0];

   // EMA M15
   if(CopyBuffer(h_ema_fast_m15,0, 0, 2, buf) < 2) return false; g_cache.ema_fast_m15 = buf[0];
   if(CopyBuffer(h_ema_mid_m15, 0, 0, 2, buf) < 2) return false; g_cache.ema_mid_m15  = buf[0];
   if(CopyBuffer(h_ema_slow_m15,0, 0, 2, buf) < 2) return false; g_cache.ema_slow_m15 = buf[0];

   // EMA H1 — leggo barra corrente [0] e N barre fa per slope
   double h1buf[];
   ArraySetAsSeries(h1buf, true);
   int barsNeeded = InpH1_SlopeBars + 1;
   if(CopyBuffer(h_ema_h1, 0, 0, barsNeeded, h1buf) < barsNeeded) return false;
   g_cache.ema_h1_cur  = h1buf[0];
   g_cache.ema_h1_prev = h1buf[InpH1_SlopeBars];

   // Trend H1 dalla slope dell'EMA
   double slope = g_cache.ema_h1_cur - g_cache.ema_h1_prev;
   double minSlope = _Point * 2.0;  // filtro rumore: almeno 2 punti di slope
   if     (slope >  minSlope) g_cache.trend_h1 =  1;
   else if(slope < -minSlope) g_cache.trend_h1 = -1;
   else                       g_cache.trend_h1 =  0;

   // ── Stoch RSI M1 (buffer 0=K, buffer 1=D) ──
   double sk[], sd[];
   ArraySetAsSeries(sk, true);
   ArraySetAsSeries(sd, true);
   if(CopyBuffer(h_srsi_m1, 0, 0, 4, sk) < 4) return false;
   if(CopyBuffer(h_srsi_m1, 1, 0, 4, sd) < 4) return false;
   g_cache.srsi_k      = sk[0];
   g_cache.srsi_d      = sd[0];
   g_cache.srsi_k_prev = sk[1];
   g_cache.srsi_d_prev = sd[1];

   // ── Stoch RSI M2 ──
   double sk2[], sd2[];
   ArraySetAsSeries(sk2, true);
   ArraySetAsSeries(sd2, true);
   if(CopyBuffer(h_srsi_m2, 0, 0, 2, sk2) < 2) return false;
   if(CopyBuffer(h_srsi_m2, 1, 0, 2, sd2) < 2) return false;
   g_cache.srsi_k_m2 = sk2[0];
   g_cache.srsi_d_m2 = sd2[0];

   // ── Stoch RSI M3 ──
   double sk3[], sd3[];
   ArraySetAsSeries(sk3, true);
   ArraySetAsSeries(sd3, true);
   if(CopyBuffer(h_srsi_m3, 0, 0, 2, sk3) < 2) return false;
   if(CopyBuffer(h_srsi_m3, 1, 0, 2, sd3) < 2) return false;
   g_cache.srsi_k_m3 = sk3[0];
   g_cache.srsi_d_m3 = sd3[0];

   // ── MACD M1 (buffer 0=main/MACD, buffer 1=signal) ──
   double mm[], ms[];
   ArraySetAsSeries(mm, true);
   ArraySetAsSeries(ms, true);
   if(CopyBuffer(h_macd_m1, 0, 0, 3, mm) < 3) return false;
   if(CopyBuffer(h_macd_m1, 1, 0, 3, ms) < 3) return false;
   g_cache.macd_main        = mm[0];
   g_cache.macd_signal      = ms[0];
   g_cache.macd_main_prev   = mm[1];
   g_cache.macd_signal_prev = ms[1];

   // ── DMI / ADX M1 (buffer 0=ADX, 1=DI+, 2=DI-) ──
   double adx[], dip[], dim[];
   ArraySetAsSeries(adx, true);
   ArraySetAsSeries(dip, true);
   ArraySetAsSeries(dim, true);
   if(CopyBuffer(h_dmi_m1, 0, 0, 2, adx) < 2) return false;
   if(CopyBuffer(h_dmi_m1, 1, 0, 2, dip) < 2) return false;
   if(CopyBuffer(h_dmi_m1, 2, 0, 2, dim) < 2) return false;
   g_cache.dmi_adx   = adx[0];
   g_cache.dmi_plus  = dip[0];
   g_cache.dmi_minus = dim[0];

   g_cache.valid = true;
   return true;
}

//+------------------------------------------------------------------+
//  SEZIONE 8 — FILTRI SESSIONE E BIAS
//+------------------------------------------------------------------+

bool IsSessionAllowed()
{
   if(!InpFilterSession) return true;
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return (dt.hour >= InpSessionStartH && dt.hour < InpSessionEndH);
}

// Bias M15: EMA in ordine
int GetBiasM15()
{
   if(g_cache.ema_fast_m15 > g_cache.ema_mid_m15 &&
      g_cache.ema_mid_m15  > g_cache.ema_slow_m15) return  1;
   if(g_cache.ema_fast_m15 < g_cache.ema_mid_m15 &&
      g_cache.ema_mid_m15  < g_cache.ema_slow_m15) return -1;
   return 0;
}

// Filtro trend H1: consente BUY solo se trend_h1 >= 0, SELL solo se <= 0
bool H1FilterAllows(int direction)
{
   if(!InpUseH1Filter) return true;
   if(direction ==  1 && g_cache.trend_h1 <  0) return false; // no BUY in trend H1 ribassista
   if(direction == -1 && g_cache.trend_h1 >  0) return false; // no SELL in trend H1 rialzista
   return true;
}

//+------------------------------------------------------------------+
//  SEZIONE 9 — GENERATORE SEGNALI
//+------------------------------------------------------------------+

bool GenerateSignal(SSignal &sig)
{
   if(!g_cache.valid) return false;

   int  bias      = GetBiasM15();
   bool anySignal = false;

   if((InpStrategyMask & MODE_SCALP1) != 0 && !SignalCooldown(0))
   {
      SSignal s; if(CheckSCALP1(s, bias)) { MergeSignal(sig, s); anySignal = true; }
   }
   if((InpStrategyMask & MODE_SCALP2) != 0 && !SignalCooldown(1))
   {
      SSignal s; if(CheckSCALP2(s, bias)) { MergeSignal(sig, s); anySignal = true; }
   }
   if((InpStrategyMask & MODE_SCALP3) != 0 && !SignalCooldown(2))
   {
      SSignal s; if(CheckSCALP3(s, bias)) { MergeSignal(sig, s); anySignal = true; }
   }
   if((InpStrategyMask & MODE_MTF1) != 0 && !SignalCooldown(3))
   {
      SSignal s; if(CheckMTF1(s, bias)) { MergeSignal(sig, s); anySignal = true; }
   }
   if((InpStrategyMask & MODE_MTF2) != 0 && !SignalCooldown(4))
   {
      SSignal s; if(CheckMTF2(s, bias)) { MergeSignal(sig, s); anySignal = true; }
   }
   if((InpStrategyMask & MODE_SMC1) != 0 && !SignalCooldown(5))
   {
      SSignal s; if(CheckSMC1(s, bias)) { MergeSignal(sig, s); anySignal = true; }
   }

   return anySignal && sig.direction != SIG_NONE;
}

void MergeSignal(SSignal &m, const SSignal &inc)
{
   if(m.direction == SIG_NONE)          { m = inc; return; }
   if(m.direction != inc.direction)     { m.direction = SIG_NONE; return; }
   if((int)inc.quality < (int)m.quality) m.quality = inc.quality;
   m.strategyMask |= inc.strategyMask;
   m.comment += "|" + inc.comment;
}

bool SignalCooldown(int idx)
{
   return (g_lastSignalTime[idx] == iTime(_Symbol, PERIOD_M1, 0));
}
void MarkSignal(int idx)
{
   g_lastSignalTime[idx] = iTime(_Symbol, PERIOD_M1, 0);
}

//+------------------------------------------------------------------+
//  SEZIONE 10 — HELPER INDICATORI
//+------------------------------------------------------------------+

// MACD cross sulla candela chiusa [1]
bool MACDCrossUp()
{
   return (g_cache.macd_main_prev < g_cache.macd_signal_prev &&
           g_cache.macd_main      > g_cache.macd_signal);
}
bool MACDCrossDown()
{
   return (g_cache.macd_main_prev > g_cache.macd_signal_prev &&
           g_cache.macd_main      < g_cache.macd_signal);
}

// K cross D su Stoch RSI M1
bool SRSICrossUp()
{
   return (g_cache.srsi_k_prev < g_cache.srsi_d_prev &&
           g_cache.srsi_k      > g_cache.srsi_d);
}
bool SRSICrossDown()
{
   return (g_cache.srsi_k_prev > g_cache.srsi_d_prev &&
           g_cache.srsi_k      < g_cache.srsi_d);
}

bool StochOB_M1()  { return g_cache.srsi_k > InpStochOB; }
bool StochOS_M1()  { return g_cache.srsi_k < InpStochOS; }
bool MACD_OB()     { return g_cache.macd_main >  InpMACDThreshold; }
bool MACD_OS()     { return g_cache.macd_main < -InpMACDThreshold; }
bool EMABull_M1()  { return g_cache.ema_fast > g_cache.ema_mid && g_cache.ema_mid > g_cache.ema_slow; }
bool EMABear_M1()  { return g_cache.ema_fast < g_cache.ema_mid && g_cache.ema_mid < g_cache.ema_slow; }

//+------------------------------------------------------------------+
//  SEZIONE 11 — STRATEGIE
//+------------------------------------------------------------------+

// ── SCALP1: Stoch RSI OS/OB + MACD cross + confluenza MACD zona ──
bool CheckSCALP1(SSignal &sig, int bias)
{
   sig.strategyMask = MODE_SCALP1; sig.suggestedTP = 0; sig.suggestedSL = 0;

   // Long: Stoch RSI in OS + MACD cross rialzista + MACD in zona OS
   if(g_cache.srsi_k < InpStochOS && MACD_OS() && MACDCrossUp() &&
      bias >= 0 && H1FilterAllows(1))
   {
      sig.direction = SIG_BUY;
      sig.quality   = EMABull_M1() ? LOT_FULL : LOT_ONE_THIRD;
      sig.comment   = "SCALP1-B";
      MarkSignal(0); return true;
   }
   // Short
   if(g_cache.srsi_k > InpStochOB && MACD_OB() && MACDCrossDown() &&
      bias <= 0 && H1FilterAllows(-1))
   {
      sig.direction = SIG_SELL;
      sig.quality   = EMABear_M1() ? LOT_FULL : LOT_ONE_THIRD;
      sig.comment   = "SCALP1-S";
      MarkSignal(0); return true;
   }
   return false;
}

// ── SCALP2: EMA in ordine + DMI + K cross D come trigger ──
bool CheckSCALP2(SSignal &sig, int bias)
{
   sig.strategyMask = MODE_SCALP2; sig.suggestedTP = 0; sig.suggestedSL = 0;

   bool dmiOk = (g_cache.dmi_adx > 20.0);
   double price = g_cache.close[0];

   if(EMABull_M1() && price > g_cache.ema_fast && dmiOk &&
      g_cache.dmi_plus > g_cache.dmi_minus && SRSICrossUp() &&
      g_cache.srsi_k < InpStochOB && H1FilterAllows(1))
   {
      sig.direction = SIG_BUY;
      sig.quality   = LOT_ONE_THIRD;
      sig.comment   = "SCALP2-B";
      MarkSignal(1); return true;
   }
   if(EMABear_M1() && price < g_cache.ema_fast && dmiOk &&
      g_cache.dmi_minus > g_cache.dmi_plus && SRSICrossDown() &&
      g_cache.srsi_k > InpStochOS && H1FilterAllows(-1))
   {
      sig.direction = SIG_SELL;
      sig.quality   = LOT_ONE_THIRD;
      sig.comment   = "SCALP2-S";
      MarkSignal(1); return true;
   }
   return false;
}

// ── SCALP3: Divergenze hidden/regular su Stoch RSI (OFF di default) ──
bool CheckSCALP3(SSignal &sig, int bias)
{
   sig.strategyMask = MODE_SCALP3; sig.suggestedTP = 0; sig.suggestedSL = 0;

   double p0 = g_cache.close[0];
   double p2 = g_cache.close[2];
   double k0 = g_cache.srsi_k;
   double k1 = g_cache.srsi_k_prev;
   double ps  = _Point;

   bool nearEMA = (MathAbs(p0 - g_cache.ema_fast) < 8*ps ||
                   MathAbs(p0 - g_cache.ema_mid)  < 12*ps);

   // Hidden bull: prezzo minimo più alto, K più basso
   bool hiddenBull = (p0 > p2) && (k0 < k1) && (k0 < 50.0) && nearEMA;
   // Hidden bear: prezzo massimo più basso, K più alto
   bool hiddenBear = (p0 < p2) && (k0 > k1) && (k0 > 50.0) && nearEMA;
   // Regular bull: prezzo nuovo minimo, K no (inversione)
   bool regularBull= (p0 < p2) && (k0 > k1) && (k0 < InpStochOS);
   // Regular bear: prezzo nuovo massimo, K no
   bool regularBear= (p0 > p2) && (k0 < k1) && (k0 > InpStochOB);

   if((hiddenBull || regularBull) && bias >= 0 && H1FilterAllows(1))
   {
      sig.direction = SIG_BUY;
      sig.quality   = regularBull ? LOT_FULL : LOT_TWO_THIRD;
      sig.comment   = regularBull ? "SCALP3-RegB" : "SCALP3-HidB";
      MarkSignal(2); return true;
   }
   if((hiddenBear || regularBear) && bias <= 0 && H1FilterAllows(-1))
   {
      sig.direction = SIG_SELL;
      sig.quality   = regularBear ? LOT_FULL : LOT_TWO_THIRD;
      sig.comment   = regularBear ? "SCALP3-RegS" : "SCALP3-HidS";
      MarkSignal(2); return true;
   }
   return false;
}

// ── MTF1: 3x Stoch RSI allineati su M1/M2/M3 ──
// Con indicatore nativo le zone < 15 / > 85 corrispondono davvero a
// ipervenduto/ipercomprato estremo sul RSI — molto più preciso di iStochastic.
bool CheckMTF1(SSignal &sig, int bias)
{
   sig.strategyMask = MODE_MTF1; sig.suggestedTP = 0; sig.suggestedSL = 0;

   double k1  = g_cache.srsi_k;
   double k2  = g_cache.srsi_k_m2;
   double k3  = g_cache.srsi_k_m3;
   double ob  = InpMTF1_OB;
   double os_ = InpMTF1_OS;

   // Confluenza piena (tutti e 3 in zona estrema) + K cross D su M1
   bool fullOS = (k1 < os_ && k2 < os_ && k3 < os_);
   bool fullOB = (k1 > ob  && k2 > ob  && k3 > ob);

   // Confluenza parziale (M1 estremo + M2/M3 convergenti)
   bool partOS = (k1 < os_ && k2 < 40.0 && k3 < 40.0);
   bool partOB = (k1 > ob  && k2 > 60.0 && k3 > 60.0);

   if(fullOS && SRSICrossUp() && H1FilterAllows(1))
   {
      sig.direction = SIG_BUY;
      sig.quality   = LOT_TWO_THIRD;
      sig.comment   = "MTF1-fullOS";
      MarkSignal(3); return true;
   }
   if(fullOB && SRSICrossDown() && H1FilterAllows(-1))
   {
      sig.direction = SIG_SELL;
      sig.quality   = LOT_TWO_THIRD;
      sig.comment   = "MTF1-fullOB";
      MarkSignal(3); return true;
   }
   if(partOS && SRSICrossUp() && H1FilterAllows(1))
   {
      sig.direction = SIG_BUY;
      sig.quality   = LOT_ONE_THIRD;
      sig.comment   = "MTF1-partOS";
      MarkSignal(3); return true;
   }
   if(partOB && SRSICrossDown() && H1FilterAllows(-1))
   {
      sig.direction = SIG_SELL;
      sig.quality   = LOT_ONE_THIRD;
      sig.comment   = "MTF1-partOB";
      MarkSignal(3); return true;
   }
   return false;
}

// ── MTF2: Divergenze d'Oro — EMA in ordine + hidden div. + MACD cross ──
bool CheckMTF2(SSignal &sig, int bias)
{
   sig.strategyMask = MODE_MTF2;

   if(!EMABull_M1() && !EMABear_M1()) return false;

   double p0 = g_cache.close[0];
   double p2 = g_cache.close[2];
   double k0 = g_cache.srsi_k;
   double k1 = g_cache.srsi_k_prev;

   // Calcola ampiezza impulso → TP proiettato.
   // Fix v2.01: InpTP_DefaultPips e' espresso in pips; su XAU 1 pip = 10 point.
   // La vecchia formula divideva per _Point e generava TP/SL enormi (es. 200 USD su Gold).
   double impulse = 0;
   for(int i = 1; i <= 4; i++)
      impulse = MathMax(impulse, g_cache.high[i] - g_cache.low[i]);
   double defaultTpPts = InpMTF2UseFixedPipUnits
                         ? InpTP_DefaultPips * 10.0
                         : InpTP_DefaultPips / _Point * 10.0;
   double tpPts = MathMax(impulse / _Point, defaultTpPts);
   double slPts = tpPts * 0.60; // SL = 60% del TP

   bool hidBull = EMABull_M1() && (p0 > p2) && (k0 < k1) && MACDCrossUp()  && H1FilterAllows(1);
   bool hidBear = EMABear_M1() && (p0 < p2) && (k0 > k1) && MACDCrossDown()&& H1FilterAllows(-1);

   if(hidBull)
   {
      sig.direction   = SIG_BUY;
      sig.quality     = LOT_FULL;
      sig.suggestedTP = tpPts;
      sig.suggestedSL = slPts;
      sig.comment     = "MTF2-DivOro-B";
      MarkSignal(4); return true;
   }
   if(hidBear)
   {
      sig.direction   = SIG_SELL;
      sig.quality     = LOT_FULL;
      sig.suggestedTP = tpPts;
      sig.suggestedSL = slPts;
      sig.comment     = "MTF2-DivOro-S";
      MarkSignal(4); return true;
   }
   return false;
}

// ── SMC1: Inefficienze d'Oro — pattern 3 candele M15 ──
bool CheckSMC1(SSignal &sig, int bias)
{
   sig.strategyMask = MODE_SMC1;

   double hi2=g_cache.high_m15[2], hi1=g_cache.high_m15[1], hi0=g_cache.high_m15[0];
   double lo2=g_cache.low_m15[2],  lo1=g_cache.low_m15[1],  lo0=g_cache.low_m15[0];
   double cl2=g_cache.close_m15[2],cl1=g_cache.close_m15[1];

   // Pattern SHORT M15: [2] nuovo max con ombra → [1] rompe ma chiude sotto → [0] sotto lo1
   bool patShort = (hi2 > g_cache.high_m15[3]) && (hi1 > hi2) && (cl1 < hi2) && (lo0 < lo1);
   // Pattern LONG M15: speculare
   bool patLong  = (lo2 < g_cache.low_m15[3])  && (lo1 < lo2) && (cl1 > lo2) && (hi0 > hi1);

   if(patShort && bias <= 0 && H1FilterAllows(-1))
   {
      sig.direction   = SIG_SELL;
      sig.quality     = LOT_TWO_THIRD;
      sig.suggestedSL = 15.0 / _Point;
      sig.suggestedTP = 10.0 / _Point * 10.0;
      sig.comment     = "SMC1-IneffS";
      MarkSignal(5); return true;
   }
   if(patLong && bias >= 0 && H1FilterAllows(1))
   {
      sig.direction   = SIG_BUY;
      sig.quality     = LOT_TWO_THIRD;
      sig.suggestedSL = 15.0 / _Point;
      sig.suggestedTP = 10.0 / _Point * 10.0;
      sig.comment     = "SMC1-IneffB";
      MarkSignal(5); return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//  SEZIONE 12 — LOT CALCULATOR
//+------------------------------------------------------------------+

double CalcLot(ENUM_LOT_QUALITY quality)
{
   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double tickVal   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double pipVal    = (tickSize > 0) ? tickVal / tickSize * _Point : 0;
   if(pipVal <= 0) return InpLotMin;

   double riskPct = InpRiskPerPipPct / 100.0;
   double qualMul = (quality == LOT_FULL) ? 1.0 : (quality == LOT_TWO_THIRD) ? (2.0/3.0) : (1.0/3.0);

   // Rischio per 10 pip come riferimento
   double riskUSD = balance * riskPct * qualMul;
   double lot     = riskUSD / (10.0 * pipVal);

   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   lot = MathFloor(lot / step) * step;
   return MathMin(MathMax(lot, InpLotMin), InpLotMax);
}

//+------------------------------------------------------------------+
//  SEZIONE 13 — ESECUZIONE ORDINI
//+------------------------------------------------------------------+

void ExecuteSignal(SSignal &sig)
{
   // ── REVERSE: inverte la direzione prima di tutto il resto ──
   if(InpReverseSignals && sig.direction != SIG_NONE)
      sig.direction = (sig.direction == SIG_BUY) ? SIG_SELL : SIG_BUY;

   // ── Guard 1: cooldown globale — una apertura per candela M1 ──
   datetime curBar = iTime(_Symbol, PERIOD_M1, 0);
   if(g_lastTradeBar == curBar)
   {
      if(InpVerboseLog)
         Print("EA: skip apertura — già aperto su questa candela M1.");
      return;
   }

   // ── Guard 2: limite trade giornalieri ──
   if(InpMaxDailyTrades > 0 && g_dailyTradeCount >= InpMaxDailyTrades)
   {
      if(InpVerboseLog)
         Print("EA: limite giornaliero raggiunto (", g_dailyTradeCount, "/", InpMaxDailyTrades, ").");
      return;
   }

   // ── Guard 3: limite posizioni totali (controllo più robusto) ──
   if(CountAllOpen() >= InpMaxOpenTrades) return;

   // ── Guard 4: limite per strategia ──
   if(CountOpenByMask(sig.strategyMask) >= InpMaxTradesPerSig) return;

   double lot = CalcLot(sig.quality);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   double slPts = (sig.suggestedSL > 0) ? sig.suggestedSL : InpSL_DefaultPips * 10.0;
   double tpPts = (sig.suggestedTP > 0) ? sig.suggestedTP : InpTP_DefaultPips * 10.0 * 2.0;

   int magic = MagicFromMask(sig.strategyMask);
   g_trade.SetExpertMagicNumber(magic);

   bool opened = false;
   double sl, tp;

   if(sig.direction == SIG_BUY)
   {
      sl = ask - slPts * _Point;
      tp = ask + tpPts * _Point;
      opened = g_trade.Buy(lot, _Symbol, ask, sl, tp, sig.comment);
      if(opened)
         Print("EA BUY  | ", sig.comment,
               " | lot=", DoubleToString(lot, 2),
               " | sl=",  DoubleToString(sl, _Digits),
               " | tp=",  DoubleToString(tp, _Digits),
               " | magic=", magic);
   }
   else
   {
      sl = bid + slPts * _Point;
      tp = bid - tpPts * _Point;
      opened = g_trade.Sell(lot, _Symbol, bid, sl, tp, sig.comment);
      if(opened)
         Print("EA SELL | ", sig.comment,
               " | lot=", DoubleToString(lot, 2),
               " | sl=",  DoubleToString(sl, _Digits),
               " | tp=",  DoubleToString(tp, _Digits),
               " | magic=", magic);
   }

   // Aggiorna cooldown globale e contatore solo se l'ordine è andato a buon fine
   if(opened)
   {
      g_lastTradeBar = curBar;
      g_dailyTradeCount++;
      Print("EA: trade giornalieri oggi: ", g_dailyTradeCount,
            "/", (InpMaxDailyTrades > 0 ? (string)InpMaxDailyTrades : "∞"));
   }
}

//+------------------------------------------------------------------+
//  SEZIONE 14 — GESTIONE POSIZIONI APERTE
//+------------------------------------------------------------------+

void ManageOpenPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      int magic = (int)PositionGetInteger(POSITION_MAGIC);
      if(!IsOurMagic(magic)) continue;

      double openP  = PositionGetDouble(POSITION_PRICE_OPEN);
      double curSL  = PositionGetDouble(POSITION_SL);
      double curTP  = PositionGetDouble(POSITION_TP);
      double profit = PositionGetDouble(POSITION_PROFIT);
      double lots   = PositionGetDouble(POSITION_VOLUME);
      ENUM_POSITION_TYPE pt = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

      // ── Break Even dopo TP1 (50% del range) ──
      if(InpMoveToBE && curTP > 0 && curSL != openP)
      {
         double halfTP, newSL;
         bool tp1Hit = false;

         if(pt == POSITION_TYPE_BUY)
         {
            halfTP = openP + (curTP - openP) * 0.5;
            tp1Hit = (bid >= halfTP);
            newSL  = openP + InpBEBuffer * _Point;
            if(tp1Hit && newSL > curSL) g_trade.PositionModify(ticket, newSL, curTP);
         }
         else
         {
            halfTP = openP - (openP - curTP) * 0.5;
            tp1Hit = (ask <= halfTP);
            newSL  = openP - InpBEBuffer * _Point;
            if(tp1Hit && newSL < curSL) g_trade.PositionModify(ticket, newSL, curTP);
         }
      }

      // ── SL dinamico su Stoch RSI per SCALP1 / SCALP3 ──
      // Se K supera la soglia opposta mentre la posizione è in perdita → chiudi
      if(magic == InpMagicSCALP1 || magic == InpMagicSCALP3)
      {
         bool dynClose = false;
         if(pt == POSITION_TYPE_BUY  && g_cache.srsi_k > InpStochOB + 5.0 && profit < 0) dynClose = true;
         if(pt == POSITION_TYPE_SELL && g_cache.srsi_k < InpStochOS - 5.0 && profit < 0) dynClose = true;
         if(dynClose)
         {
            g_trade.PositionClose(ticket);
            if(InpVerboseLog) Print("EA: SL dinamico Stoch RSI ticket=", ticket);
            continue;
         }
      }

      // ── Zona rossa MTF2: uscita anticipata se minimo strutturale ──
      if(magic == InpMagicMTF2 && curSL > 0)
      {
         bool redZone = false;
         if(pt == POSITION_TYPE_BUY  && g_cache.close[0] < openP && g_cache.close[0] > curSL) redZone = true;
         if(pt == POSITION_TYPE_SELL && g_cache.close[0] > openP && g_cache.close[0] < curSL) redZone = true;
         if(redZone && profit < 0)
         {
            g_trade.PositionClose(ticket);
            if(InpVerboseLog) Print("EA MTF2: zona rossa, uscita anticipata ticket=", ticket);
         }
      }
   }
}

//+------------------------------------------------------------------+
//  SEZIONE 15 — HEDGE ENGINE
//+------------------------------------------------------------------+

void RunHedgeEngine()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   if(balance <= 0) return;

   double ddPct = (balance - equity) / balance * 100.0;
   if(ddPct > g_maxDDReached) g_maxDDReached = ddPct;

   // Circuit breaker
   if(ddPct >= InpMaxDDPct)
   {
      if(!g_circuitBreakerOn)
      {
         g_circuitBreakerOn = true;
         Print("SpaghettiEA: CIRCUIT BREAKER — DD=", DoubleToString(ddPct,2), "%");
      }
      return;
   }

   if(ddPct < InpHedgeTriggerPct) return;
   if(HasHedgePosition()) return;

   // Verifica margine libero
   double freeM = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double usedM = AccountInfoDouble(ACCOUNT_MARGIN);
   if(usedM > 0 && freeM < usedM * 0.5)
   {
      if(InpVerboseLog) Print("EA Hedge: margine insufficiente, skip.");
      return;
   }

   ENUM_SIGNAL_DIR hDir = GetHedgeDirection();
   if(hDir == SIG_NONE) return;

   // Richiede segnale Stoch RSI allineato per il hedge
   bool hSigOk = (hDir == SIG_BUY && StochOS_M1()) || (hDir == SIG_SELL && StochOB_M1());
   if(!hSigOk) return;

   double lot = CalcLot(LOT_ONE_THIRD);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   g_trade.SetExpertMagicNumber(InpMagicHEDGE);
   if(hDir == SIG_BUY)  g_trade.Buy (lot, _Symbol, ask, 0, 0, "HEDGE");
   else                 g_trade.Sell(lot, _Symbol, bid, 0, 0, "HEDGE");

   if(InpVerboseLog)
      Print("EA Hedge: ", (hDir==SIG_BUY?"BUY":"SELL"), " lot=", lot, " DD=", DoubleToString(ddPct,2), "%");
}

// Squeeze: chiude hedge in profit → parzializza 10% posizione principale
void SqueezeHedgeProfit(ulong hedgeTicket)
{
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0 || t == hedgeTicket) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(!IsOurMagic((int)PositionGetInteger(POSITION_MAGIC))) continue;
      if(PositionGetDouble(POSITION_PROFIT) >= 0) continue;

      double lots  = PositionGetDouble(POSITION_VOLUME);
      double close = MathMax(NormalizeDouble(lots * InpPartialPctHedge / 100.0, 2), InpLotMin);
      double step  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      close = MathFloor(close / step) * step;

      if(close >= lots) g_trade.PositionClose(t);
      else if(close >= InpLotMin) g_trade.PositionClosePartial(t, close);
      break;
   }
}

ENUM_SIGNAL_DIR GetHedgeDirection()
{
   double maxLoss = 0;
   ENUM_POSITION_TYPE worst = POSITION_TYPE_BUY;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      int mg = (int)PositionGetInteger(POSITION_MAGIC);
      if(mg == InpMagicHEDGE || !IsOurMagic(mg)) continue;
      double p = PositionGetDouble(POSITION_PROFIT);
      if(p < maxLoss) { maxLoss = p; worst = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE); }
   }
   if(maxLoss == 0) return SIG_NONE;
   return (worst == POSITION_TYPE_BUY) ? SIG_SELL : SIG_BUY;
}

bool HasHedgePosition()
{
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) == InpMagicHEDGE) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//  SEZIONE 16 — SAFETY + RESET GIORNALIERO
//+------------------------------------------------------------------+

bool SafetyCheck()
{
   if(g_circuitBreakerOn) return false;
   double usedM = AccountInfoDouble(ACCOUNT_MARGIN);
   double freeM = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(usedM > 0 && freeM < usedM * 0.3)
   {
      if(InpVerboseLog) Print("EA: margine critico, skip segnale.");
      return false;
   }
   return true;
}

void CheckDayReset()
{
   MqlDateTime now, last;
   TimeToStruct(TimeCurrent(), now);
   TimeToStruct(g_lastDayReset, last);
   if(now.day != last.day || now.mon != last.mon)
   {
      g_dayStartBalance  = AccountInfoDouble(ACCOUNT_BALANCE);
      g_maxDDReached     = 0.0;
      g_circuitBreakerOn = false;
      g_lastDayReset     = TimeCurrent();
      g_lastTradeBar     = 0;
      g_dailyTradeCount  = 0;
      Print("SpaghettiEA: reset giornaliero. Balance=", g_dayStartBalance);
   }
}

//+------------------------------------------------------------------+
//  SEZIONE 17 — UTILITY
//+------------------------------------------------------------------+

int MagicFromMask(int mask)
{
   if((mask & MODE_SCALP1) != 0) return InpMagicSCALP1;
   if((mask & MODE_SCALP2) != 0) return InpMagicSCALP2;
   if((mask & MODE_SCALP3) != 0) return InpMagicSCALP3;
   if((mask & MODE_MTF1)   != 0) return InpMagicMTF1;
   if((mask & MODE_MTF2)   != 0) return InpMagicMTF2;
   if((mask & MODE_SMC1)   != 0) return InpMagicSMC1;
   return InpMagicSCALP1;
}

bool IsOurMagic(int magic)
{
   return (magic == InpMagicSCALP1 || magic == InpMagicSCALP2 ||
           magic == InpMagicSCALP3 || magic == InpMagicMTF1   ||
           magic == InpMagicMTF2   || magic == InpMagicSMC1   ||
           magic == InpMagicHEDGE);
}

int CountAllOpen()
{
   int c = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(IsOurMagic((int)PositionGetInteger(POSITION_MAGIC))) c++;
   }
   return c;
}

int CountOpenByMask(int mask)
{
   int mg = MagicFromMask(mask), c = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) == mg) c++;
   }
   return c;
}

//+------------------------------------------------------------------+
//  Fine SpaghettiForex_EA.mq5  v2.00
//+------------------------------------------------------------------+
