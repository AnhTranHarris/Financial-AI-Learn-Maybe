#property strict
#property script_show_inputs

input string InpOutputFile="dusty-chart-objects.csv";

void WriteObjectAnchors(const int file,const long chart_id,const string name,const ENUM_OBJECT type)
  {
   for(int anchor=0;anchor<4;anchor++)
     {
      ResetLastError();
      long at=ObjectGetInteger(chart_id,name,OBJPROP_TIME,anchor);
      int time_error=GetLastError();
      ResetLastError();
      double price=ObjectGetDouble(chart_id,name,OBJPROP_PRICE,anchor);
      int price_error=GetLastError();
      if(time_error!=0 && price_error!=0)
         break;
      FileWrite(file,"dusty-chart-v1",TerminalInfoInteger(TERMINAL_BUILD),chart_id,ChartSymbol(chart_id),EnumToString(ChartPeriod(chart_id)),name,EnumToString(type),"anchor",anchor,at,DoubleToString(price,12));
     }
  }

void WriteObjectLevels(const int file,const long chart_id,const string name,const ENUM_OBJECT type)
  {
   int levels=(int)ObjectGetInteger(chart_id,name,OBJPROP_LEVELS);
   for(int level=0;level<levels;level++)
     {
      double value=ObjectGetDouble(chart_id,name,OBJPROP_LEVELVALUE,level);
      FileWrite(file,"dusty-chart-v1",TerminalInfoInteger(TERMINAL_BUILD),chart_id,ChartSymbol(chart_id),EnumToString(ChartPeriod(chart_id)),name,EnumToString(type),"level",level,0,DoubleToString(value,12));
     }
  }

void OnStart()
  {
   int file=FileOpen(InpOutputFile,FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON);
   if(file==INVALID_HANDLE)
      return;
   FileWrite(file,"schema","terminal_build","chart_id","symbol","timeframe","object_name","object_type","row_type","row_index","time_epoch","price_or_level");
   long chart_id=ChartFirst();
   while(chart_id>=0 && !IsStopped())
     {
      int total=ObjectsTotal(chart_id,-1,-1);
      for(int index=0;index<total;index++)
        {
         string name=ObjectName(chart_id,index,-1,-1);
         if(name=="")
            continue;
         ENUM_OBJECT type=(ENUM_OBJECT)ObjectGetInteger(chart_id,name,OBJPROP_TYPE);
         WriteObjectAnchors(file,chart_id,name,type);
         WriteObjectLevels(file,chart_id,name,type);
        }
      int windows=(int)ChartGetInteger(chart_id,CHART_WINDOWS_TOTAL);
      for(int window=0;window<windows;window++)
        {
         int indicators=ChartIndicatorsTotal(chart_id,window);
         for(int indicator=0;indicator<indicators;indicator++)
           {
            string short_name=ChartIndicatorName(chart_id,window,indicator);
            FileWrite(file,"dusty-chart-v1",TerminalInfoInteger(TERMINAL_BUILD),chart_id,ChartSymbol(chart_id),EnumToString(ChartPeriod(chart_id)),short_name,"INDICATOR","indicator",window,0,indicator);
           }
        }
      chart_id=ChartNext(chart_id);
     }
   FileClose(file);
  }

