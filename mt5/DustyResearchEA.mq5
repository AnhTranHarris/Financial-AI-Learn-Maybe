#property strict

#include <Trade/Trade.mqh>

// DustyResearchEA is intentionally Strategy-Tester-only.
// Strategy semantics are decided upstream by Dusty's Python StrategySpecV2 runtime.
// This EA consumes a deterministic manifest to measure MetaTrader execution mechanics
// without maintaining a second indicator/strategy implementation in MQL5.
// M161 transport uses FILE_COMMON so the controlling terminal and isolated local tester
// agent exchange immutable input/native evidence through one documented shared sandbox.

input string InpManifestFile = "dusty_manifest.csv";
input string InpDealsFile = "dusty_deals.csv";
input string InpStrategyHash = "";
input ulong  InpMagic = 667001;
input ulong  InpDeviationPoints = 20;

struct PlannedTrade
  {
   string   trade_id;
   datetime entry_time;
   datetime exit_time;
   bool     is_long;
   double   volume;
   double   sl;
   double   tp;
   ulong    position_ticket;
   bool     opened;
   bool     finished;
  };

CTrade Trade;
PlannedTrade Plans[];

bool TradeResultAccepted()
  {
   const uint code=Trade.ResultRetcode();
   return(code==TRADE_RETCODE_DONE || code==TRADE_RETCODE_PLACED || code==TRADE_RETCODE_DONE_PARTIAL);
  }

string DealTypeName(const long value)
  {
   if(value==DEAL_TYPE_BUY) return "buy";
   if(value==DEAL_TYPE_SELL) return "sell";
   return "other";
  }

string DealEntryName(const long value)
  {
   if(value==DEAL_ENTRY_IN) return "in";
   if(value==DEAL_ENTRY_OUT) return "out";
   if(value==DEAL_ENTRY_INOUT) return "inout";
   if(value==DEAL_ENTRY_OUT_BY) return "out_by";
   return "other";
  }

string DealReasonName(const long value)
  {
   if(value==DEAL_REASON_EXPERT) return "expert";
   if(value==DEAL_REASON_SL) return "sl";
   if(value==DEAL_REASON_TP) return "tp";
   if(value==DEAL_REASON_SO) return "stopout";
   if(value==DEAL_REASON_ROLLOVER) return "rollover";
   if(value==DEAL_REASON_CLIENT) return "client";
   if(value==DEAL_REASON_MOBILE) return "mobile";
   if(value==DEAL_REASON_WEB) return "web";
   return "other";
  }

ulong FindPlanPosition(const string trade_id)
  {
   const string expected_comment="DDT:"+trade_id;
   for(int i=0;i<PositionsTotal();i++)
     {
      const ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket))
         continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC)!=InpMagic)
         continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol)
         continue;
      if(PositionGetString(POSITION_COMMENT)!=expected_comment)
         continue;
      return ticket;
     }
   return 0;
  }

bool LoadManifest()
  {
   const int handle=FileOpen(InpManifestFile,FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ,',');
   if(handle==INVALID_HANDLE)
     {
      PrintFormat("DustyResearchEA cannot open common manifest %s. Error=%d",InpManifestFile,GetLastError());
      return false;
     }

   // deterministic seven-column header
   for(int col=0;col<7 && !FileIsEnding(handle);col++)
      FileReadString(handle);

   ArrayResize(Plans,0);
   while(!FileIsEnding(handle))
     {
      PlannedTrade row;
      row.trade_id=FileReadString(handle);
      if(row.trade_id=="")
         break;
      row.entry_time=StringToTime(FileReadString(handle));
      row.exit_time=StringToTime(FileReadString(handle));
      const string side=StringToLower(FileReadString(handle));
      row.is_long=(side=="long");
      if(!row.is_long && side!="short")
        {
         FileClose(handle);
         PrintFormat("DustyResearchEA invalid side for %s",row.trade_id);
         return false;
        }
      row.volume=FileReadNumber(handle);
      row.sl=FileReadNumber(handle);
      row.tp=FileReadNumber(handle);
      row.position_ticket=0;
      row.opened=false;
      row.finished=false;
      if(row.entry_time<=0 || row.exit_time<=row.entry_time || row.volume<=0 || row.sl<=0 || row.tp<0)
        {
         FileClose(handle);
         PrintFormat("DustyResearchEA invalid manifest row %s",row.trade_id);
         return false;
        }
      const int next=ArraySize(Plans)+1;
      ArrayResize(Plans,next);
      Plans[next-1]=row;
     }
   FileClose(handle);
   return ArraySize(Plans)>0;
  }

int OnInit()
  {
   // Hard safety boundary: this EA cannot initialize on a normal terminal chart.
   if(!MQLInfoInteger(MQL_TESTER))
     {
      Print("DustyResearchEA refuses to run outside MetaTrader Strategy Tester");
      return INIT_FAILED;
     }
   if(InpStrategyHash=="")
     {
      Print("DustyResearchEA requires InpStrategyHash");
      return INIT_PARAMETERS_INCORRECT;
     }
   Trade.SetExpertMagicNumber(InpMagic);
   Trade.SetDeviationInPoints(InpDeviationPoints);
   Trade.SetAsyncMode(false);
   Trade.SetTypeFillingBySymbol(_Symbol);
   if(!LoadManifest())
      return INIT_FAILED;
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   const datetime now=TimeCurrent();
   for(int i=0;i<ArraySize(Plans);i++)
     {
      if(Plans[i].finished)
         continue;

      if(!Plans[i].opened)
        {
         if(now<Plans[i].entry_time)
            continue;
         const string comment="DDT:"+Plans[i].trade_id;
         bool requested=false;
         if(Plans[i].is_long)
            requested=Trade.Buy(Plans[i].volume,_Symbol,0.0,Plans[i].sl,Plans[i].tp,comment);
         else
            requested=Trade.Sell(Plans[i].volume,_Symbol,0.0,Plans[i].sl,Plans[i].tp,comment);
         if(!requested || !TradeResultAccepted())
           {
            PrintFormat("DustyResearchEA entry failed %s retcode=%u",Plans[i].trade_id,Trade.ResultRetcode());
            Plans[i].finished=true;
            continue;
           }
         Plans[i].opened=true;
         Plans[i].position_ticket=FindPlanPosition(Plans[i].trade_id);
        }

      if(Plans[i].position_ticket==0)
         Plans[i].position_ticket=FindPlanPosition(Plans[i].trade_id);

      if(Plans[i].opened && Plans[i].position_ticket==0)
        {
         // Position may already have been closed by its broker-native SL/TP.
         Plans[i].finished=true;
         continue;
        }

      if(now>=Plans[i].exit_time && Plans[i].position_ticket>0)
        {
         const bool requested=Trade.PositionClose(Plans[i].position_ticket,InpDeviationPoints);
         if(!requested || !TradeResultAccepted())
           {
            PrintFormat("DustyResearchEA timed close failed %s ticket=%I64u retcode=%u",
                        Plans[i].trade_id,Plans[i].position_ticket,Trade.ResultRetcode());
            continue;
           }
         Plans[i].finished=true;
        }
     }
  }

void ExportDeals()
  {
   if(!HistorySelect(0,TimeCurrent()+86400))
      return;
   const int handle=FileOpen(InpDealsFile,FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,',');
   if(handle==INVALID_HANDLE)
     {
      PrintFormat("DustyResearchEA cannot open common deal export %s. Error=%d",InpDealsFile,GetLastError());
      return;
     }
   FileWrite(handle,
             "terminal_build","symbol","period","strategy_hash","position_id","deal_id","time_msc",
             "deal_type","deal_type_name","entry_type","entry_type_name","volume","price","commission",
             "swap","profit","fee","reason","reason_name","sl","tp","comment");
   for(int i=0;i<HistoryDealsTotal();i++)
     {
      const ulong deal=HistoryDealGetTicket(i);
      if(deal==0)
         continue;
      if((ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=InpMagic)
         continue;
      const long deal_type=HistoryDealGetInteger(deal,DEAL_TYPE);
      if(deal_type!=DEAL_TYPE_BUY && deal_type!=DEAL_TYPE_SELL)
         continue;
      const long entry_type=HistoryDealGetInteger(deal,DEAL_ENTRY);
      const long reason=HistoryDealGetInteger(deal,DEAL_REASON);
      FileWrite(handle,
                TerminalInfoInteger(TERMINAL_BUILD),
                _Symbol,
                EnumToString((ENUM_TIMEFRAMES)_Period),
                InpStrategyHash,
                HistoryDealGetInteger(deal,DEAL_POSITION_ID),
                deal,
                HistoryDealGetInteger(deal,DEAL_TIME_MSC),
                deal_type,
                DealTypeName(deal_type),
                entry_type,
                DealEntryName(entry_type),
                HistoryDealGetDouble(deal,DEAL_VOLUME),
                HistoryDealGetDouble(deal,DEAL_PRICE),
                HistoryDealGetDouble(deal,DEAL_COMMISSION),
                HistoryDealGetDouble(deal,DEAL_SWAP),
                HistoryDealGetDouble(deal,DEAL_PROFIT),
                HistoryDealGetDouble(deal,DEAL_FEE),
                reason,
                DealReasonName(reason),
                HistoryDealGetDouble(deal,DEAL_SL),
                HistoryDealGetDouble(deal,DEAL_TP),
                HistoryDealGetString(deal,DEAL_COMMENT));
     }
   FileFlush(handle);
   FileClose(handle);
  }

void OnDeinit(const int reason)
  {
   ExportDeals();
  }
