#property strict

// Tester-only indicator parity probe. It never sends orders.
input string InpOutputFile = "dusty_indicator_parity.csv";
input int InpMAPeriod = 20;
input int InpATRPeriod = 14;
input int InpRSIPeriod = 14;

int HSMA=INVALID_HANDLE;
int HEMA=INVALID_HANDLE;
int HATR=INVALID_HANDLE;
int HRSI=INVALID_HANDLE;
int Out=INVALID_HANDLE;
datetime LastClosedBar=0;

bool ReadOne(const int handle,const int shift,double &value)
  {
   double buffer[1];
   const int copied=CopyBuffer(handle,0,shift,1,buffer);
   if(copied!=1 || !MathIsValidNumber(buffer[0]))
      return false;
   value=buffer[0];
   return true;
  }

int OnInit()
  {
   if(!MQLInfoInteger(MQL_TESTER))
     {
      Print("DustyIndicatorParity refuses to run outside Strategy Tester");
      return INIT_FAILED;
     }
   if(InpMAPeriod<2 || InpATRPeriod<2 || InpRSIPeriod<2)
      return INIT_PARAMETERS_INCORRECT;
   HSMA=iMA(_Symbol,_Period,InpMAPeriod,0,MODE_SMA,PRICE_CLOSE);
   HEMA=iMA(_Symbol,_Period,InpMAPeriod,0,MODE_EMA,PRICE_CLOSE);
   HATR=iATR(_Symbol,_Period,InpATRPeriod);
   HRSI=iRSI(_Symbol,_Period,InpRSIPeriod,PRICE_CLOSE);
   if(HSMA==INVALID_HANDLE || HEMA==INVALID_HANDLE || HATR==INVALID_HANDLE || HRSI==INVALID_HANDLE)
      return INIT_FAILED;
   Out=FileOpen(InpOutputFile,FILE_WRITE|FILE_CSV|FILE_ANSI,',');
   if(Out==INVALID_HANDLE)
      return INIT_FAILED;
   // source_open_time is the completed bar's own opening time (shift 1).
   // available_time is the newly opened current bar (shift 0), the first deterministic point
   // at which this probe treats the previous bar's final OHLC/indicator values as observable.
   FileWrite(Out,"source_open_time","available_time","sma","ema","atr","rsi");
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   const datetime closed_open=iTime(_Symbol,_Period,1);
   const datetime available=iTime(_Symbol,_Period,0);
   if(closed_open<=0 || available<=closed_open || closed_open==LastClosedBar)
      return;
   double sma=0.0,ema=0.0,atr=0.0,rsi=0.0;
   if(!ReadOne(HSMA,1,sma) || !ReadOne(HEMA,1,ema) || !ReadOne(HATR,1,atr) || !ReadOne(HRSI,1,rsi))
      return;
   FileWrite(Out,(long)closed_open,(long)available,sma,ema,atr,rsi);
   FileFlush(Out);
   LastClosedBar=closed_open;
  }

void OnDeinit(const int reason)
  {
   if(Out!=INVALID_HANDLE)
      FileClose(Out);
   if(HSMA!=INVALID_HANDLE) IndicatorRelease(HSMA);
   if(HEMA!=INVALID_HANDLE) IndicatorRelease(HEMA);
   if(HATR!=INVALID_HANDLE) IndicatorRelease(HATR);
   if(HRSI!=INVALID_HANDLE) IndicatorRelease(HRSI);
  }
