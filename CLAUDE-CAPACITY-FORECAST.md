# CLAUDE-CAPACITY-FORECAST.md

> Companion to the top-level `CLAUDE.md` — read that first for tech
> stack, blueprint layout, Redis key conventions, and the planned
> audit-log work package this doc depends on.
>
> **Feature:** Per-pool utilization time-series and exhaustion ETA
> projections, sourced from audit-log history.
> **Scope:** new `forecast.py` blueprint, one statistics module,
> dashboard view, integration with the migration dry-run.
> **Status:** Design — not yet implemented. **Hard dependency:** the
> audit-log work package from top-level `CLAUDE.md` must land first.

---

## Goal

Once the audit log exists, every allocation has a timestamp. Roll that
forward into "this pool will exhaust on date X at current growth rate"
and "this pool has been flat for 6 months — consider downsizing." Both
directions are operationally valuable; both are pure regression over
existing audit data.

End-state:

1. **Per-pool utilization time-series** — fraction-used over time,
   smoothed to daily / weekly samples.
2. **Exhaustion ETA** — linear (or simple exponential) projection of
   when utilization crosses 90% / 95% / 100%, with a confidence band.
3. **Stagnation flag** — pools with no allocation events in the last
   N days, candidates for the right-sizing report from
   `CLAUDE-POOL-MIGRATION.md`.
4. **Dashboard** — fleet view sorted by urgency.

---

## Non-goals

- **No ML / neural forecasting.** Linear and simple exponential
  smoothing covers the operationally useful cases. The variance in IP
  allocation patterns rarely justifies anything fancier, and
  black-box forecasts erode operator trust.
- **No anomaly detection.** A spike isn't an anomaly to be
  highlighted; it's a signal the operator already knows about. Out of
  scope as a separate feature.
- **No seasonal decomposition.** Most IPAM growth isn't seasonal in a
  way that matters at IP-allocation timescales. Add later only if
  real data shows obvious quarterly patterns.
- **No auto-resize action.** Forecast informs; operator acts.
- **No predictions for new pools.** Pools without history return
  "insufficient data" rather than wild extrapolations.

---

## Hard dependencies

The audit log must provide, at minimum:

- Per-allocation events: `{pool_id, network_id, ip, ts, action}`
  where action ∈ `claimed`, `released`.
- Per-pool capacity changes: `{pool_id, cidrs_before, cidrs_after, ts}`
  for tracking pool resizes.

These are already shapes the top-level audit-log package plans to
emit. This doc reads from them — no new writes.

If the audit log isn't live, this entire feature returns "no data
yet"; nothing breaks, but no forecasts. Document the dependency
clearly on the dashboard.

---

## Algorithm

### Time-series construction

For each pool, build a daily utilization series over the last N days
(default 180):

```python
def utilization_series(pool_id: str, days: int = 180) -> list[tuple[date, float]]:
    """Return [(date, fraction_used)] sampled daily."""
    events = audit_log.events_for_pool(pool_id, since=days_ago(days))
    capacity_events = audit_log.capacity_events_for_pool(pool_id,
                                                        since=days_ago(days))

    # Walk events chronologically, maintaining current allocated count
    # and current capacity. Sample utilization at each day boundary.
    samples = []
    allocated = audit_log.allocated_count_at(pool_id, days_ago(days))
    capacity  = audit_log.capacity_at(pool_id, days_ago(days))
    cursor    = days_ago(days)

    for day in date_range(days_ago(days), today()):
        # Apply all events with ts <= day
        while events and events[0]['ts'].date() <= day:
            e = events.pop(0)
            allocated += 1 if e['action'] == 'claimed' else -1
        while capacity_events and capacity_events[0]['ts'].date() <= day:
            c = capacity_events.pop(0)
            capacity = sum_capacity(c['cidrs_after'])
        samples.append((day, allocated / max(capacity, 1)))

    return samples
```

Capacity changes are handled as step-function transitions: a /24 → /23
resize halves utilization immediately on the resize date.

### Linear regression (primary)

Fit `utilization = m * days_since_start + b` over the most recent
window (default 90 days):

```python
def linear_forecast(series: list[tuple[date, float]],
                    window: int = 90) -> dict:
    """Return {slope, intercept, r_squared, eta_90, eta_95, eta_100}."""
    recent = series[-window:]
    if len(recent) < 14:
        return {'status': 'insufficient_data'}

    xs = [(d - recent[0][0]).days for d, _ in recent]
    ys = [u for _, u in recent]
    slope, intercept, r2 = _ordinary_least_squares(xs, ys)

    today_x = xs[-1]
    if slope <= 0:
        return {'status': 'stable_or_declining',
                'slope': slope, 'r_squared': r2}

    return {
        'status':    'growing',
        'slope':     slope,
        'intercept': intercept,
        'r_squared': r2,
        'eta_90':    recent[0][0] + timedelta(days=(0.90 - intercept) / slope),
        'eta_95':    recent[0][0] + timedelta(days=(0.95 - intercept) / slope),
        'eta_100':   recent[0][0] + timedelta(days=(1.00 - intercept) / slope),
    }
```

`r_squared` doubles as a confidence indicator — when r² < 0.5, the
trend is noisy and ETAs are unreliable. The UI surfaces this as a
visible confidence chip ("low confidence — high variance").

### Exponential smoothing (secondary)

For pools that grow non-linearly (occasional bulk allocations,
periodic prunes), single-exponential smoothing gives a better
projection at the cost of explainability. Offer it as a "smoothing"
toggle on the dashboard; default off.

```python
def exponential_forecast(series, alpha: float = 0.3) -> dict:
    """Single exponential smoothing for non-linear growth."""
    ...
```

Don't auto-select between linear and exponential — let the operator
choose. Auto-selection would be a fourth layer of magic that erodes
trust the moment it picks wrong.

### Stagnation detection

A pool with zero claim events in the last `stagnation_threshold`
days (default 90) and utilization > 0 is flagged as stagnant. Hint
the operator to run the migration dry-run from
`CLAUDE-POOL-MIGRATION.md` to see how much could be reclaimed.

---

## Routes

```
GET /forecast                            — dashboard
GET /forecast/pools/<pid>                — per-pool detail
GET /api/forecast/pools/<pid>.json       — JSON payload
GET /api/forecast/series/<pid>.json      — raw time-series
```

JSON payloads are structured for plotting libraries:

```json
{
  "pool_id":   "pool-...",
  "series":    [{"date": "2026-01-01", "util": 0.42}, ...],
  "forecast": {
    "model":      "linear",
    "status":     "growing",
    "slope":      0.0023,
    "r_squared":  0.87,
    "eta_90":     "2026-08-15",
    "eta_95":     "2026-09-30",
    "eta_100":    "2026-11-12"
  },
  "stagnant":  false
}
```

---

## UI

### Forecast dashboard (`/forecast`)

Table sorted by urgency (ETA 90% ascending, with stagnant pools at
the bottom):

```
Pool                          Util    Trend    ETA 90%      Confidence
prod/transit  (proj-A)        78.4%   ↑ 0.23%  2026-07-12   high (r²=.91)
prod/loopback (proj-A)        45.2%   ↑ 0.08%  2027-02-04   medium
mgmt/oob      (proj-B)        12.0%   →        —            stagnant (96d)
test/lab      (proj-C)        91.5%   ↓ 0.05%  declining    —
...
```

Filters by project, VRF, label-set. Trend arrows are derived from
slope sign; ETA cell is blank for declining/stagnant pools.

### Per-pool detail (`/forecast/pools/<pid>`)

Three panels:

1. **Plot** — utilization series with the linear fit overlaid, ETA
   markers at 90/95/100%. Rendered via a small inline SVG (no JS
   chart library; the IPAM keeps its no-build-pipeline posture).
2. **Stats** — current util, slope, r², ETAs, model selector
   (linear / exponential).
3. **Suggested action** — "ETA 95% in 6 weeks: consider growing the
   pool" or "stagnant 4 months: see migration report for reclaim
   estimate" with a link to `CLAUDE-POOL-MIGRATION.md`'s dry-run.

### Sparkline on pool detail page

The existing pool page (from `CLAUDE-POOL-OPTIMIZATION.md`'s
`_pool_widget.html`) gains a small 90-day sparkline + ETA-90% chip.
Cheap eye-grab for "is this pool in trouble."

---

## Edge cases

- **No audit-log history.** Pool returns `status: "insufficient_data"`
  with a hint to wait or check that auditing is enabled.
- **New pool with one allocation.** Same as above; need at least 14
  days of variation to fit.
- **Pool resized during the window.** Utilization step at the resize
  date is genuine, not noise. The fit window should include only
  post-resize data when a recent (within `window` days) resize
  happened — surface this as "fit reset due to recent resize."
- **Pool emptied (all allocations released).** Slope goes negative;
  status becomes `stable_or_declining`. No ETA emitted. Useful
  signal for "this pool's role has changed; might be deletable."
- **Pool over 100%.** Shouldn't happen (allocation should fail), but
  if audit data shows it (legacy data, manual edits), cap displayed
  util at 100% and emit a separate "over-capacity audit anomaly"
  warning that links to `CLAUDE-CROSS-PROJECT-LINT.md`.
- **Spiky burst pattern.** A pool that fills in chunks (monthly batch
  provisioning) has high variance with low underlying slope. R²
  surfaces this; UI labels as "bursty" when r² < 0.5 and stdev is
  high. Exponential smoothing handles this better; suggest the model
  switch.
- **Pools with manual allocations only.** Linear fit still works;
  results may be less meaningful. Same as for any other pool — the
  algorithm doesn't care about allocation source.

---

## Composability with other docs

- **`CLAUDE-POOL-MIGRATION.md`** — stagnant pools link directly to
  the dry-run report for that project, pre-filtered. Tight loop:
  "this pool hasn't grown in 4 months → here's what aggregate sizing
  would have reserved → here's the reclaim estimate."
- **`CLAUDE-POOL-OPTIMIZATION.md`** — the recommend-prefix helper
  optionally takes forecast input: "current util + 90-day growth ×
  buffer factor = recommended size." Opt-in; defaults stay
  forecast-blind.
- **`CLAUDE-POOL-OBSERVABILITY.md`** — adds `forecast.compute` event
  with pool_id, model, status, r², ETAs, and series length.
- **Audit log work package** — this entire feature is a consumer.

---

## Testing

- `tests/unit/test_forecast_series.py` — series construction over a
  synthetic audit log including pool resizes, releases, and gaps.
- `tests/unit/test_forecast_linear.py` — OLS math against a known
  reference, edge cases (constant, monotone, noisy).
- `tests/unit/test_forecast_exponential.py` — smoothing math.
- `tests/unit/test_forecast_stagnation.py` — threshold behavior.
- `tests/integration/test_forecast_routes.py` — dashboard rendering,
  JSON API.

Synthetic audit logs are cheap to construct; no real audit-log
implementation needed for unit tests. Integration tests can stub
`audit_log.events_for_pool` until the real package lands.

---

## Implementation order

Cannot start before the audit-log package emits the required events.
Once it does:

1. **`utilization_series` + linear forecast.** Pure functions over
   audit data, fully testable in isolation.
2. **Per-pool JSON API.** Internal-only; validate against
   hand-curated test pools.
3. **Per-pool detail page** with inline-SVG plot.
4. **Dashboard view** with sorting and filtering.
5. **Stagnation detection + integration with migration dry-run.**
6. **Sparkline on pool widget.**
7. **Exponential smoothing model** as an opt-in toggle.

Each step is independently shippable. No data migration (read-only
over audit data).

---

## Related docs

- `CLAUDE.md` — top-level reference, audit-log work package
  (required dependency).
- `CLAUDE-POOL-MIGRATION.md` — destination of stagnation hints.
- `CLAUDE-POOL-OPTIMIZATION.md` — optional forecast input to
  recommend-prefix.
- `CLAUDE-CROSS-PROJECT-LINT.md` — destination of over-capacity
  audit anomalies.
- `CLAUDE-POOL-OBSERVABILITY.md` — event taxonomy for
  `forecast.compute`.
