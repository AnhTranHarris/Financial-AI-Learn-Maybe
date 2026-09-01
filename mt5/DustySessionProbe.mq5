#property strict
#property script_show_inputs

input string InpOutputFile="dusty-market-sessions.csv";

void WriteSessions(const int file,const string kind)
  {
   for(int day=0;day<7;day++)
     {
      for(uint index=0;index<32;index++)
        {
         datetime session_from=0;
         datetime session_to=0;
         bool found=false;
         if(kind=="trade")
            found=SymbolInfoSessionTrade(_Symbol,(ENUM_DAY_OF_WEEK)day,index,session_from,session_to);
         else
            found=SymbolInfoSessionQuote(_Symbol,(ENUM_DAY_OF_WEEK)day,index,session_from,session_to);
         if(!found)
            break;
         // MQL5 weekday: Sunday=0. Dusty's datetime contract: Monday=0.
         int python_weekday=(day+6)%7;
         MqlTick tick;
         long last_tick=0;
         if(SymbolInfoTick(_Symbol,tick))
            last_tick=(long)tick.time;
         FileWrite(
            file,
            "dusty-session-v1",
            TerminalInfoInteger(TERMINAL_BUILD),
            TerminalInfoString(TERMINAL_COMPANY),
            AccountInfoString(ACCOUNT_SERVER),
            _Symbol,
            (long)TimeGMT(),
            (long)(TimeTradeServer()-TimeGMT()),
            kind,
            python_weekday,
            index,
            (long)session_from,
            (long)session_to,
            (long)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_MODE),
            last_tick
         );
        }
     }
  }

void OnStart()
  {
   int file=FileOpen(InpOutputFile,FILE_WRITE|FILE_CSV|FILE_ANSI,',');
   if(file==INVALID_HANDLE)
      return;
   FileWrite(file,"schema","terminal_build","broker","server","symbol","captured_epoch","utc_offset_seconds","kind","weekday","session_index","from_seconds","to_seconds","trade_mode","last_tick_epoch");
   WriteSessions(file,"trade");
   WriteSessions(file,"quote");
   FileClose(file);
  }
