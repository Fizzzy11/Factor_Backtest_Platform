# Factor Backtest Framework Implementation Plan

> 历史说明：本文记录 Classic 初始实现计划，文中的 `factor_backtest` 路径仅代表 Classic 时代的包名，不是 Platform 1.0.0 的现行导入规范。Platform 现行包名为 `factor_backtest_platform`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first runnable version of the factor ranking backtest framework described in `docs/superpowers/specs/2026-05-19-factor-backtest-framework-design.md`.

**Architecture:** Implement a small Python package with focused modules for config, factor loading, trading calendars, pool loading, market data containers, filtering, analytics, report sections, and orchestration. Computation writes intermediate artifacts first, then report sections render from saved results. ClickHouse integration is isolated behind an adapter and not required for local tests.

**Tech Stack:** Python, pandas, numpy, pytest, matplotlib optional for plot rendering.

---

### Task 1: Project Skeleton And Configuration

**Files:**
- Create: `pyproject.toml`
- Create: `factor_backtest/__init__.py`
- Create: `factor_backtest/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_config.py` with tests that import `BacktestConfig`, check default paths, default pools, horizons, colors, and pool registry entries.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`

Expected: FAIL because package and config do not exist.

- [ ] **Step 3: Implement config**

Create dataclasses for `PathConfig`, `BacktestConfig`, `PoolDefinition`, default `POOL_REGISTRY`, and `DEFAULT_HORIZON_COLORS`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`

Expected: PASS.

### Task 2: Factor Loader

**Files:**
- Create: `factor_backtest/factor_loader.py`
- Test: `tests/test_factor_loader.py`

- [ ] **Step 1: Write failing tests**

Tests cover wide DataFrame normalization, MultiIndex `(date, asset)` conversion, long table conversion, and default path discovery from `factor_name`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_factor_loader.py -v`

Expected: FAIL because loader functions do not exist.

- [ ] **Step 3: Implement factor loader**

Implement `normalize_factor_dataframe`, `resolve_factor_path`, and `load_factor_file`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_factor_loader.py -v`

Expected: PASS.

### Task 3: Calendar, Pool, And Market Data

**Files:**
- Create: `factor_backtest/calendar.py`
- Create: `factor_backtest/pools.py`
- Create: `factor_backtest/market_data.py`
- Test: `tests/test_calendar_pools_market_data.py`

- [ ] **Step 1: Write failing tests**

Tests cover trade-date shifting, lookback windows, long-form pool CSV conversion into masks, `all` virtual pool behavior, and `listed_days` derivation.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calendar_pools_market_data.py -v`

Expected: FAIL because modules do not exist.

- [ ] **Step 3: Implement modules**

Implement `TradingCalendar`, `load_pool_mask`, `resolve_selected_pools`, `MarketDataBundle`, and `derive_listed_days_from_open`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calendar_pools_market_data.py -v`

Expected: PASS.

### Task 4: Filters And Analytics

**Files:**
- Create: `factor_backtest/filters.py`
- Create: `factor_backtest/analytics.py`
- Test: `tests/test_filters_analytics.py`

- [ ] **Step 1: Write failing tests**

Tests cover tradability mask rules, open-to-open future returns, RankIC, 10-group returns, long-short returns, data quality metrics, and IC statistics.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_filters_analytics.py -v`

Expected: FAIL because functions do not exist.

- [ ] **Step 3: Implement filters and analytics**

Implement vectorized helpers for tradability masks, future returns, daily IC, grouped returns, long-short series, quality metrics, and summary stats.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_filters_analytics.py -v`

Expected: PASS.

### Task 5: Runner And Report Sections

**Files:**
- Create: `factor_backtest/sections.py`
- Create: `factor_backtest/runner.py`
- Create: `factor_backtest/io.py`
- Test: `tests/test_runner_sections.py`

- [ ] **Step 1: Write failing tests**

Tests cover pool-isolated artifact generation, module failure isolation, artifact file writing, and `render_from_artifacts` style section re-rendering.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runner_sections.py -v`

Expected: FAIL because runner does not exist.

- [ ] **Step 3: Implement runner and sections**

Implement base artifact computation and simple section framework with independent error capture.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_runner_sections.py -v`

Expected: PASS.

### Task 6: ClickHouse Adapter And Chinese README

**Files:**
- Create: `factor_backtest/clickhouse_adapter.py`
- Create: `README.md`
- Test: `tests/test_clickhouse_adapter.py`

- [ ] **Step 1: Write failing tests**

Tests verify SQL contains required fields and table names, without connecting to ClickHouse.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_clickhouse_adapter.py -v`

Expected: FAIL because adapter does not exist.

- [ ] **Step 3: Implement adapter and README**

Implement SQL builder and Chinese usage documentation.

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest -v`

Expected: PASS.

## Self-Review

This plan covers the approved design at first-version scope: factor loading, data normalization, calendar logic, pool masks, tradability filtering, RankIC, grouped returns, modular artifacts, isolated report sections, ClickHouse field mapping, and Chinese documentation. Full HTML dashboard polish and real server ClickHouse execution are intentionally deferred because local verification cannot connect to the production data service.
