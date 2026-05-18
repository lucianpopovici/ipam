### Name Expansion Patterns

Forms that create multiple entities at once support a `{start..end}` range
syntax in the name field, with zero-padding preserved from the literal digits.

**Used in:**
- Site bulk create (`templates/ne/site_bulk_form.html` + `expand_site_pattern` in `ne.py`)
- NE Type interface builder — "+ Add Interface" (`templates/ne/ne_type_form.html`)

**Pattern rules:**
| Input | Result |
|-------|--------|
| `ran{0001..1200}` | `ran0001`, `ran0002`, …, `ran1200` (4-digit padding preserved) |
| `site-{01..10}-prod` | `site-01-prod`, …, `site-10-prod` |
| `eth{0..7}` | `eth0`, `eth1`, …, `eth7` |
| `gi-{N}/0` (with count=3) | `gi-0/0`, `gi-1/0`, `gi-2/0` (`{N}` placeholder + count box) |
| `eth` (with count=4, no pattern) | `eth0`, `eth1`, `eth2`, `eth3` (index appended) |

**Implementation notes:**
- Server-side: reuse `expand_site_pattern(pattern)` in `ne.py` — returns
  `(names, error)` and caps ranges at 10,000.
- Client-side: the regex is `/\{(\d+)\.\.(\d+)\}/`. The interface builder uses
  a smaller cap (1024) since interface counts per NE are realistically small.
- When a `{start..end}` range is present, it always wins over any separate
  count input — derive the count from the range.

When adding a new "bulk create" form, follow this same convention rather than
inventing a new syntax.
