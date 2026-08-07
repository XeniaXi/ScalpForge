#property strict
#property version   "1.10"
#property description "Read-only ScalpForge demo quote recorder. Never sends orders."

input string OutputPrefix = "scalpforge";
input int FlushEveryTicks = 25;
input int HeartbeatSeconds = 10;

int tickHandle = INVALID_HANDLE;
int tickCounter = 0;
long recordSequence = 0;
string sessionId = "";
string openUtcDay = "";

string CsvSafe(string value)
{
   StringReplace(value, ",", "_");
   StringReplace(value, "\r", " ");
   StringReplace(value, "\n", " ");
   return value;
}

string UtcNow()
{
   return TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS);
}

string ServerNow()
{
   return TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS);
}

string UtcDay()
{
   string value = TimeToString(TimeGMT(), TIME_DATE);
   StringReplace(value, ".", "");
   return value;
}

bool OpenDailyFile()
{
   string day = UtcDay();
   if(tickHandle != INVALID_HANDLE && day == openUtcDay)
      return true;
   if(tickHandle != INVALID_HANDLE)
   {
      FileFlush(tickHandle);
      FileClose(tickHandle);
   }
   string filename = OutputPrefix + "_" + Symbol() + "_" + day + "_ticks.csv";
   tickHandle = FileOpen(filename, FILE_CSV | FILE_READ | FILE_WRITE | FILE_COMMON | FILE_SHARE_READ, ',');
   if(tickHandle == INVALID_HANDLE)
   {
      Print("ScalpForge could not open daily tick file: ", GetLastError());
      return false;
   }
   openUtcDay = day;
   FileSeek(tickHandle, 0, SEEK_END);
   WriteHeaderIfEmpty(tickHandle);
   return true;
}

void WriteHeaderIfEmpty(int handle)
{
   if(FileSize(handle) == 0)
      FileWrite(handle, "record_type", "received_utc", "server_time", "monotonic_ms",
                "session_id", "source_sequence", "broker", "server", "symbol",
                "bid", "ask", "spread_points");
}

void WriteRecord(string recordType)
{
   if(!OpenDailyFile())
      return;
   RefreshRates();
   FileWrite(tickHandle, recordType, UtcNow(), ServerNow(), IntegerToString(GetTickCount()),
             sessionId, IntegerToString(recordSequence),
             CsvSafe(AccountCompany()), CsvSafe(AccountServer()), CsvSafe(Symbol()),
             DoubleToString(Bid, Digits), DoubleToString(Ask, Digits),
             DoubleToString((Ask - Bid) / Point, 2));
   recordSequence++;
   tickCounter++;
   if(tickCounter >= FlushEveryTicks || recordType == "heartbeat")
   {
      FileFlush(tickHandle);
      tickCounter = 0;
      // MT4 can keep directory metadata cached while a file remains open. Closing on the
      // heartbeat makes completed records visible to an external read-only collector.
      if(recordType == "heartbeat")
      {
         FileClose(tickHandle);
         tickHandle = INVALID_HANDLE;
      }
   }
}

void WriteSymbolSpecification()
{
   string filename = OutputPrefix + "_" + Symbol() + "_spec.csv";
   int handle = FileOpen(filename, FILE_CSV | FILE_WRITE | FILE_COMMON, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("ScalpForge could not create specification file: ", GetLastError());
      return;
   }
   FileWrite(handle, "captured_utc", "broker", "server", "symbol", "digits", "point",
             "contract_size", "min_lot", "max_lot", "lot_step", "tick_size", "tick_value",
             "stop_level_points", "freeze_level_points", "swap_long", "swap_short",
             "margin_required", "trade_allowed");
   FileWrite(handle, UtcNow(), CsvSafe(AccountCompany()), CsvSafe(AccountServer()), Symbol(),
             Digits, DoubleToString(Point, Digits), MarketInfo(Symbol(), MODE_LOTSIZE),
             MarketInfo(Symbol(), MODE_MINLOT), MarketInfo(Symbol(), MODE_MAXLOT),
             MarketInfo(Symbol(), MODE_LOTSTEP), MarketInfo(Symbol(), MODE_TICKSIZE),
             MarketInfo(Symbol(), MODE_TICKVALUE), MarketInfo(Symbol(), MODE_STOPLEVEL),
             MarketInfo(Symbol(), MODE_FREEZELEVEL), MarketInfo(Symbol(), MODE_SWAPLONG),
             MarketInfo(Symbol(), MODE_SWAPSHORT), MarketInfo(Symbol(), MODE_MARGINREQUIRED),
             MarketInfo(Symbol(), MODE_TRADEALLOWED));
   FileClose(handle);
}

int OnInit()
{
   sessionId = UtcDay() + "-" + IntegerToString(GetTickCount());
   if(!OpenDailyFile())
      return INIT_FAILED;
   WriteSymbolSpecification();
   EventSetTimer(MathMax(1, HeartbeatSeconds));
   Print("ScalpForgeRecorder active in read-only mode for ", Symbol());
   return INIT_SUCCEEDED;
}

void OnTick()
{
   WriteRecord("tick");
}

void OnTimer()
{
   WriteRecord("heartbeat");
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(tickHandle != INVALID_HANDLE)
   {
      FileFlush(tickHandle);
      FileClose(tickHandle);
      tickHandle = INVALID_HANDLE;
   }
}
