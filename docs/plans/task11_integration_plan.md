# Task #11 Integration Plan: Scratchpad → Pipeline

**Status**: Ready to implement (waiting for Task #8 completion)

## Overview

Integrate AnalysisScratchpad logging into the pipeline and commands layer to track all tool calls and reasoning steps during analysis.

## Dependencies

- ✅ Task #5: FinanceTool protocol defined
- ✅ Task #6: ToolRegistry implemented
- ✅ Task #7: FMP tools wrapped (9+ tools)
- ✅ Task #9: Registry initialized in terminal/tools/__init__.py
- ✅ Task #10: AnalysisScratchpad implemented
- ✅ Task #12: Scratchpad viewer commands added
- ⏳ Task #8: Registry integrated into pipeline (IN PROGRESS)

## Integration Points

### 1. pipeline.py: collect_data()

**Current signature (lines 167-168):**
```python
def collect_data(
    symbol: str,
    price_days: int = 60,
) -> DataPackage:
```

**New signature:**
```python
def collect_data(
    symbol: str,
    price_days: int = 60,
    scratchpad: Optional[AnalysisScratchpad] = None,
) -> DataPackage:
```

**Implementation pattern (after Task #8 completes):**
```python
from terminal.tools.registry import get_registry
from terminal.scratchpad import AnalysisScratchpad

def collect_data(symbol, price_days=60, scratchpad=None):
    registry = get_registry()
    pkg = DataPackage(symbol=symbol)

    # Log data collection start
    if scratchpad:
        scratchpad.log_reasoning("data_collection", f"Starting data collection for {symbol}")

    # Tool call with logging
    try:
        price_data = registry.execute("get_historical_price", symbol=symbol, years=5)
        pkg.price = price_data

        if scratchpad:
            scratchpad.log_tool_call(
                "get_historical_price",
                {"symbol": symbol, "years": 5},
                price_data
            )
    except Exception as e:
        logger.warning(f"Price data failed: {e}")
        if scratchpad:
            scratchpad.log_reasoning("error", f"get_historical_price failed: {e}")

    # Similar pattern for all other tool calls:
    # - get_company_profile
    # - get_income_statement
    # - get_balance_sheet
    # - get_cash_flow
    # - get_financial_ratios
    # etc.

    return pkg
```

### 2. commands.py: analyze_ticker()

**Current function (lines 29-93):**
```python
def analyze_ticker(symbol: str, depth: str = "quick", price_days: int = 60) -> Dict[str, Any]:
    result = {"symbol": symbol, "depth": depth}

    # Phase 1: Data collection
    data_pkg = collect_data(symbol, price_days=price_days)
    result["data"] = {...}

    # Phase 2: Lens prompts (standard/full)
    if depth in ("standard", "full"):
        prompts = prepare_lens_prompts(symbol, data_pkg)
        result["lens_prompts"] = prompts

    # Phase 3: Debate + memo (full)
    if depth == "full":
        result["debate_instructions"] = "..."
        result["memo_skeleton"] = prepare_memo_skeleton(symbol)

    return result
```

**Modified with scratchpad:**
```python
def analyze_ticker(symbol: str, depth: str = "quick", price_days: int = 60) -> Dict[str, Any]:
    from terminal.scratchpad import AnalysisScratchpad

    symbol = symbol.upper()
    result = {"symbol": symbol, "depth": depth}

    # Create scratchpad
    scratchpad = AnalysisScratchpad(symbol, depth)

    try:
        # Phase 1: Data collection
        scratchpad.log_reasoning("phase_1_start", f"Collecting data for {symbol} (depth={depth})")
        data_pkg = collect_data(symbol, price_days=price_days, scratchpad=scratchpad)

        result["data"] = {...}
        scratchpad.log_reasoning("phase_1_complete", f"Data collection complete. Has financials: {data_pkg.has_financials}")

        # Phase 2: Lens prompts
        if depth in ("standard", "full"):
            scratchpad.log_reasoning("phase_2_start", f"Preparing {len(lenses)} lens prompts")
            prompts = prepare_lens_prompts(symbol, data_pkg)
            result["lens_prompts"] = prompts
            # Note: Actual lens execution happens in conversation, logged separately via lens_complete()

        # Phase 3: Debate + memo
        if depth == "full":
            scratchpad.log_reasoning("phase_3_start", "Full depth: debate + memo generation enabled")
            result["debate_instructions"] = "..."
            result["memo_skeleton"] = prepare_memo_skeleton(symbol)

        # Return scratchpad path
        result["scratchpad_path"] = str(scratchpad.log_path)

    except Exception as e:
        # Log error before raising
        scratchpad.log_reasoning("error", f"Analysis failed: {str(e)}")
        raise

    return result
```

## Tool Call Mapping

Based on pipeline.py current implementation (lines 184-206), tools to log:

1. **get_stock_data()** → Will be broken into multiple registry.execute() calls by Task #8:
   - get_historical_price
   - get_company_profile
   - get_income_statement
   - get_balance_sheet
   - get_cash_flow
   - get_financial_ratios

2. **run_indicators()** → Could remain as-is or be wrapped as tool:
   - Option A: Keep as direct call, log as "run_indicators" tool
   - Option B: Wrap as IndicatorTool (future enhancement)

## Reasoning Log Points

Key moments to log reasoning (beyond tool calls):

1. **Phase transitions**:
   - "phase_1_start": Data collection begins
   - "phase_1_complete": Data collection done, financials status
   - "phase_2_start": Lens prompt generation (if standard/full)
   - "phase_3_start": Debate + memo enabled (if full)

2. **Error handling**:
   - "error": Any exception with description

3. **Data quality checks**:
   - "data_quality": Missing financials, incomplete data warnings

## Testing Strategy

After implementation:

1. **Unit tests** (add to tests/test_pipeline.py):
   ```python
   def test_collect_data_with_scratchpad(tmp_path, monkeypatch):
       monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)
       scratchpad = AnalysisScratchpad("AAPL", "quick")

       data_pkg = collect_data("AAPL", price_days=60, scratchpad=scratchpad)

       events = read_scratchpad(scratchpad.log_path)
       tool_events = [e for e in events if e["type"] == "tool_call"]

       # Should have logged all tool calls
       assert len(tool_events) >= 5  # price, profile, income, balance, cash flow
   ```

2. **Integration test** (manual):
   ```python
   from terminal.commands import analyze_ticker
   from terminal.scratchpad import read_scratchpad

   result = analyze_ticker("NVDA", depth="standard")
   log_path = result["scratchpad_path"]

   events = read_scratchpad(Path(log_path))
   print(f"Total events: {len(events)}")
   print(f"Tool calls: {sum(1 for e in events if e['type'] == 'tool_call')}")
   ```

3. **End-to-end test** (full depth):
   ```python
   # Run full analysis, check scratchpad completeness
   result = analyze_ticker("TSLA", depth="full")
   replay = replay_analysis_scratchpad(result["scratchpad_path"])

   assert replay["stats"]["tool_calls"] >= 5
   assert replay["stats"]["reasoning_steps"] >= 3
   assert "phase_1_complete" in [e["step"] for e in ... if e["type"] == "reasoning"]
   ```

## Success Criteria

- ✅ collect_data() accepts optional scratchpad parameter
- ✅ All tool calls logged with args and results
- ✅ Phase transitions logged as reasoning steps
- ✅ Errors logged before raising
- ✅ analyze_ticker() creates scratchpad and returns path
- ✅ Existing tests still pass (backward compatible)
- ✅ New tests cover scratchpad integration
- ✅ Manual smoke test: analyze_ticker("NVDA", "full") generates complete scratchpad

## Timeline

**Estimated effort**: 1-2 hours after Task #8 completes

1. Modify pipeline.py collect_data() signature and add logging (30 min)
2. Modify commands.py analyze_ticker() to create scratchpad (20 min)
3. Add tests (30 min)
4. Manual testing and debugging (20 min)

## Notes

- **Backward compatibility**: scratchpad parameter is optional (default None)
- **No behavior changes**: Analysis logic unchanged, only adds logging
- **Performance**: JSONL append is fast, minimal overhead
- **Storage**: Scratchpads automatically organized by symbol in data/companies/
- **Cleanup**: No automatic cleanup needed (logs are git-ignored, human-reviewable)

---

**Ready to implement immediately after Task #8 completion notification.**
