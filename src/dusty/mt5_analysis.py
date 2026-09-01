from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Iterable

from .analytical_tools import AnalyticalToolSpec, ToolKind, ToolParameter


class NativeIndicator(StrEnum):
    SMA = "sma"
    EMA = "ema"
    ATR = "atr"
    RSI = "rsi"
    MACD = "macd"
    ADX = "adx"
    BOLLINGER_BANDS = "bollinger_bands"
    STOCHASTIC = "stochastic"
    CCI = "cci"
    MOMENTUM = "momentum"
    OBV = "obv"
    MFI = "mfi"
    ICHIMOKU = "ichimoku"
    STANDARD_DEVIATION = "standard_deviation"


_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./\\ -]+$")
_SAFE_SYMBOL = re.compile(r"^[A-Za-z0-9_.+#-]+$")
_TIMEFRAMES = frozenset(
    {
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "M6",
        "M10",
        "M12",
        "M15",
        "M20",
        "M30",
        "H1",
        "H2",
        "H3",
        "H4",
        "H6",
        "H8",
        "H12",
        "D1",
        "W1",
        "MN1",
    }
)


@dataclass(frozen=True, slots=True)
class IndicatorProbeRequest:
    tool: AnalyticalToolSpec
    symbol: str
    timeframe: str
    native_indicator: NativeIndicator | None = None
    max_bars: int = 10_000

    def __post_init__(self) -> None:
        if not _SAFE_SYMBOL.fullmatch(self.symbol) or self.timeframe not in _TIMEFRAMES:
            raise ValueError("indicator probe requires safe symbol and MT5 timeframe")
        if self.max_bars < 1 or self.max_bars > 1_000_000:
            raise ValueError("indicator probe bar budget is invalid")
        if self.tool.kind is ToolKind.NATIVE_INDICATOR and self.native_indicator is None:
            raise ValueError("native indicator probe requires native indicator identity")
        if self.tool.kind is ToolKind.CUSTOM_INDICATOR and self.native_indicator is not None:
            raise ValueError("custom indicator probe cannot declare native identity")
        if self.tool.kind not in {ToolKind.NATIVE_INDICATOR, ToolKind.CUSTOM_INDICATOR}:
            raise ValueError("indicator probe requires an indicator tool")


def render_indicator_probe(request: IndicatorProbeRequest) -> str:
    """Render a tester-only, per-tool MQL5 probe with literal dependency identity.

    Per-tool generation is intentional. MT5 tester dependency discovery requires a literal custom
    indicator name or ``tester_indicator`` declaration; one opaque dynamic loader would not provide
    a trustworthy, reproducible tester package.
    """
    handle_expression, tester_property = _handle_expression(request)
    buffers = ",".join(str(row.index) for row in request.tool.buffers)
    return f'''#property strict
{tester_property}// Dusty tool fingerprint: {request.tool.fingerprint}
input string InpOutputFile="dusty-indicator-{request.tool.fingerprint[:12]}.csv";
int Handle=INVALID_HANDLE;
int Buffers[]={{{buffers}}};
int WrittenBars=0;

int OnInit()
  {{
   if(!MQLInfoInteger(MQL_TESTER))
      return(INIT_FAILED);
   if(_Symbol!="{request.symbol}" || _Period!=PERIOD_{request.timeframe})
      return(INIT_FAILED);
   Handle={handle_expression};
   if(Handle==INVALID_HANDLE)
      return(INIT_FAILED);
   return(INIT_SUCCEEDED);
  }}

void OnDeinit(const int reason)
  {{
   if(Handle!=INVALID_HANDLE)
      IndicatorRelease(Handle);
  }}

void OnTick()
  {{
   static datetime last_open=0;
   if(WrittenBars>={request.max_bars})
      return;
   datetime source_open=iTime(_Symbol,_Period,1);
   if(source_open<=0 || source_open==last_open || BarsCalculated(Handle)<2)
      return;
   last_open=source_open;
   int file=FileOpen(InpOutputFile,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON);
   if(file==INVALID_HANDLE)
      return;
   if(FileSize(file)==0)
      FileWrite(file,"schema","terminal_build","symbol","timeframe","tool_fingerprint","source_open_epoch","available_epoch","buffer_index","value");
   FileSeek(file,0,SEEK_END);
   for(int i=0;i<ArraySize(Buffers);i++)
     {{
      double value[1];
      if(CopyBuffer(Handle,Buffers[i],1,1,value)!=1 || !MathIsValidNumber(value[0]))
         continue;
      FileWrite(file,"dusty-indicator-v1",TerminalInfoInteger(TERMINAL_BUILD),_Symbol,EnumToString(_Period),"{request.tool.fingerprint}",(long)source_open,(long)iTime(_Symbol,_Period,0),Buffers[i],DoubleToString(value[0],12));
     }}
   FileClose(file);
   WrittenBars++;
  }}
'''


def _handle_expression(request: IndicatorProbeRequest) -> tuple[str, str]:
    if request.tool.kind is ToolKind.CUSTOM_INDICATOR:
        path = request.tool.artifact_path.replace("/", "\\")
        if path.lower().startswith("mql5\\indicators\\"):
            path = path[len("MQL5\\Indicators\\") :]
        if path.lower().endswith(".ex5"):
            path = path[:-4]
        if not path or not _SAFE_PATH.fullmatch(path) or '"' in path or ".." in path.split("\\"):
            raise ValueError("custom indicator path is unsafe for generated MQL5")
        args = "".join(f",{_mql_literal(row)}" for row in request.tool.parameters)
        escaped = path.replace("\\", "\\\\")
        return f'iCustom(_Symbol,_Period,"{escaped}"{args})', f'#property tester_indicator "{escaped}.ex5"\n'

    params = {row.name: row for row in request.tool.parameters}
    indicator = request.native_indicator
    if indicator is NativeIndicator.SMA:
        return f"iMA(_Symbol,_Period,{_int(params, 'period')},0,MODE_SMA,PRICE_CLOSE)", ""
    if indicator is NativeIndicator.EMA:
        return f"iMA(_Symbol,_Period,{_int(params, 'period')},0,MODE_EMA,PRICE_CLOSE)", ""
    if indicator is NativeIndicator.ATR:
        return f"iATR(_Symbol,_Period,{_int(params, 'period')})", ""
    if indicator is NativeIndicator.RSI:
        return f"iRSI(_Symbol,_Period,{_int(params, 'period')},PRICE_CLOSE)", ""
    if indicator is NativeIndicator.MACD:
        return f"iMACD(_Symbol,_Period,{_int(params, 'fast')},{_int(params, 'slow')},{_int(params, 'signal')},PRICE_CLOSE)", ""
    if indicator is NativeIndicator.ADX:
        return f"iADX(_Symbol,_Period,{_int(params, 'period')})", ""
    if indicator is NativeIndicator.BOLLINGER_BANDS:
        return f"iBands(_Symbol,_Period,{_int(params, 'period')},0,{_float(params, 'deviation')},PRICE_CLOSE)", ""
    if indicator is NativeIndicator.STOCHASTIC:
        return f"iStochastic(_Symbol,_Period,{_int(params, 'k_period')},{_int(params, 'd_period')},{_int(params, 'slowing')},MODE_SMA,STO_LOWHIGH)", ""
    if indicator is NativeIndicator.CCI:
        return f"iCCI(_Symbol,_Period,{_int(params, 'period')},PRICE_TYPICAL)", ""
    if indicator is NativeIndicator.MOMENTUM:
        return f"iMomentum(_Symbol,_Period,{_int(params, 'period')},PRICE_CLOSE)", ""
    if indicator is NativeIndicator.OBV:
        return "iOBV(_Symbol,_Period,VOLUME_TICK)", ""
    if indicator is NativeIndicator.MFI:
        return f"iMFI(_Symbol,_Period,{_int(params, 'period')},VOLUME_TICK)", ""
    if indicator is NativeIndicator.ICHIMOKU:
        return f"iIchimoku(_Symbol,_Period,{_int(params, 'tenkan')},{_int(params, 'kijun')},{_int(params, 'senkou')})", ""
    if indicator is NativeIndicator.STANDARD_DEVIATION:
        return f"iStdDev(_Symbol,_Period,{_int(params, 'period')},0,MODE_SMA,PRICE_CLOSE)", ""
    raise ValueError("unsupported native indicator")


def _int(params: dict[str, ToolParameter], name: str) -> int:
    row = params.get(name)
    if row is None or row.value_type not in {"int", "enum"}:
        raise ValueError(f"native indicator requires integer parameter: {name}")
    value = int(row.value)
    if value <= 0:
        raise ValueError(f"native indicator parameter must be positive: {name}")
    return value


def _float(params: dict[str, ToolParameter], name: str) -> str:
    row = params.get(name)
    if row is None or row.value_type != "float":
        raise ValueError(f"native indicator requires float parameter: {name}")
    value = float(row.value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"native indicator parameter must be positive: {name}")
    return repr(value)


def _mql_literal(row: ToolParameter) -> str:
    if row.value_type == "bool":
        return "true" if row.value else "false"
    if row.value_type in {"int", "enum"}:
        return str(int(row.value))
    if row.value_type == "float":
        return repr(float(row.value))
    text = str(row.value)
    if '"' in text or "\\" in text or "\n" in text or "\r" in text:
        raise ValueError("custom indicator string parameter is unsafe")
    return f'"{text}"'


@dataclass(frozen=True, slots=True)
class NativeIndicatorRow:
    terminal_build: int
    symbol: str
    timeframe: str
    tool_fingerprint: str
    source_open: datetime
    available_at: datetime
    buffer_index: int
    value: float

    def __post_init__(self) -> None:
        if self.terminal_build < 1 or not self.symbol.strip() or not self.timeframe.strip():
            raise ValueError("native indicator row lacks environment identity")
        if len(self.tool_fingerprint) != 64 or self.buffer_index < 0 or not math.isfinite(self.value):
            raise ValueError("native indicator row is invalid")
        if self.source_open.tzinfo is None or self.available_at.tzinfo is None:
            raise ValueError("native indicator row timestamps must be aware")
        if self.available_at <= self.source_open:
            raise ValueError("indicator availability must follow source bar open")


def parse_indicator_export(text: str) -> tuple[NativeIndicatorRow, ...]:
    reader = csv.DictReader(io.StringIO(text))
    required = {
        "schema",
        "terminal_build",
        "symbol",
        "timeframe",
        "tool_fingerprint",
        "source_open_epoch",
        "available_epoch",
        "buffer_index",
        "value",
    }
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise ValueError("indicator export header is incomplete")
    rows: list[NativeIndicatorRow] = []
    for raw in reader:
        if raw["schema"] != "dusty-indicator-v1":
            raise ValueError("unsupported indicator export schema")
        rows.append(
            NativeIndicatorRow(
                int(raw["terminal_build"]),
                raw["symbol"],
                raw["timeframe"],
                raw["tool_fingerprint"],
                datetime.fromtimestamp(int(raw["source_open_epoch"]), tz=timezone.utc),
                datetime.fromtimestamp(int(raw["available_epoch"]), tz=timezone.utc),
                int(raw["buffer_index"]),
                float(raw["value"]),
            )
        )
    if not rows:
        raise ValueError("indicator export contains no values")
    identity = {(row.terminal_build, row.symbol, row.timeframe, row.tool_fingerprint) for row in rows}
    if len(identity) != 1:
        raise ValueError("indicator export mixes environment or tool identity")
    return tuple(rows)


def buffer_map(rows: Iterable[NativeIndicatorRow]) -> dict[tuple[datetime, int], float]:
    result: dict[tuple[datetime, int], float] = {}
    for row in rows:
        key = (row.source_open, row.buffer_index)
        if key in result:
            raise ValueError("duplicate native indicator value")
        result[key] = row.value
    return result


@dataclass(frozen=True, slots=True)
class NativeChartRow:
    terminal_build: int
    chart_id: int
    symbol: str
    timeframe: str
    object_name: str
    object_type: str
    row_type: str
    row_index: int
    at: datetime | None
    price_or_level: float

    def __post_init__(self) -> None:
        if self.terminal_build < 1 or self.chart_id < 0 or not self.symbol.strip() or not self.timeframe.strip():
            raise ValueError("native chart row lacks environment identity")
        if not self.object_name.strip() or not self.object_type.strip() or self.row_type not in {"anchor", "level", "indicator"}:
            raise ValueError("native chart row lacks object semantics")
        if self.row_index < 0 or not math.isfinite(self.price_or_level):
            raise ValueError("native chart row value is invalid")


def parse_chart_export(text: str) -> tuple[NativeChartRow, ...]:
    reader = csv.DictReader(io.StringIO(text))
    required = {
        "schema",
        "terminal_build",
        "chart_id",
        "symbol",
        "timeframe",
        "object_name",
        "object_type",
        "row_type",
        "row_index",
        "time_epoch",
        "price_or_level",
    }
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise ValueError("chart export header is incomplete")
    rows: list[NativeChartRow] = []
    for raw in reader:
        if raw["schema"] != "dusty-chart-v1":
            raise ValueError("unsupported chart export schema")
        epoch = int(raw["time_epoch"])
        rows.append(
            NativeChartRow(
                int(raw["terminal_build"]),
                int(raw["chart_id"]),
                raw["symbol"],
                raw["timeframe"],
                raw["object_name"],
                raw["object_type"],
                raw["row_type"],
                int(raw["row_index"]),
                None if epoch == 0 else datetime.fromtimestamp(epoch, tz=timezone.utc),
                float(raw["price_or_level"]),
            )
        )
    if not rows:
        raise ValueError("chart export contains no objects or indicators")
    return tuple(rows)
