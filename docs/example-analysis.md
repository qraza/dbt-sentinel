# dbt-sentinel report

**1 failing test(s)**

## assert_int_trips_enriched_speed_within_bounds

- **Guards:** `main.int_trips_enriched`
- **Failing rows:** 1826500
- **Confidence:** high

**Root cause**

The avg_speed_mph formula uses a multiplier of 600 instead of 60. The formula is: round(600 * trip_distance / nullif(trip_duration_minutes, 0), 2). Since trip_distance is in miles and trip_duration_minutes is in minutes, the correct conversion to miles-per-hour requires multiplying by 60 (miles / minutes * 60 = mph), not 600. The erroneous factor of 600 inflates every computed speed by exactly 10x, causing virtually all trips to exceed the 80 mph upper bound defined in the test. For example, a trip of 9.24 miles in 52 minutes yields (9.24/52)*60 = 10.66 mph (plausible), but with the bug: (9.24/52)*600 = 106.62 mph — which exactly matches the failing row. The same 10x inflation is confirmed across all sampled rows (e.g. 0.78 mi / 5 min * 600 = 93.6, /60 = 9.36 which is realistic).

**Suggested fix**

Change the multiplier in the avg_speed_mph calculation from 600 to 60 in the int_trips_enriched model:

  round(60 * t.trip_distance / nullif(t.trip_duration_minutes, 0), 2) as avg_speed_mph

This correctly converts (miles / minutes) to miles per hour.

**Evidence**

The compiled SQL shows: round(600 * t.trip_distance / nullif(t.trip_duration_minutes, 0), 2) as avg_speed_mph. Manually recomputing with the correct factor of 60 for the first failing row: 60 * 9.24 / 52 = 10.66 mph (realistic taxi speed), while 600 * 9.24 / 52 = 106.62 mph — exactly matching the reported avg_speed_mph of 106.62. This 10x inflation pattern is consistent across all 20 sampled rows, every one of which has an avg_speed_mph that is implausibly high but becomes perfectly plausible when divided by 10.
