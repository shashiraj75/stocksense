# Web memory runtime

The web lifespan installs a default executor with eight workers instead of
Python's host-dependent default (32 on the production host). `WEB_IO_WORKERS`
can override this between 1 and 32. This bounds persistent quote/IO threads;
the event loop owns executor shutdown. Other explicit pools are unaffected.
Daily Picks cron processes do not enter the web lifespan.

The Railway **StockSense360 web service only** uses `MALLOC_ARENA_MAX=2`.
This must be set before process startup. It limits glibc arena proliferation,
not the amount of memory available to the application. Keep the existing
minute-based collection/trim maintenance. Do not apply this setting to all
services implicitly. Remove the variable to restore the allocator default.

Production-image read-only comparisons on 2 October 2026:

* Equal 128 quote reads: persistent 32-worker pool ended at 167,628 KiB RSS;
  four-worker pool at 123,048 KiB. After closing pools: 127,780 / 115,868 KiB.
  Four workers took roughly 4.4–5.2 seconds per batch versus 0.9–1.7 seconds
  with 32. Eight is a concurrency/memory compromise, not a measured optimum.
* Twelve mixed index/sector/global refresh cycles: default allocator ended
  at 125,684 KiB, versus 116,760 KiB with two arenas. Both used the existing
  GC/trim policy. These isolated probes do not explain all live-process RSS.

Verify logs for `io_workers=8 malloc_arena_max=2`, API latency, publication
status, and memory after warm-up and over a full trading day. Startup memory
alone does not establish stability. Persistent application data and native
fragmentation can remain; these bounds are not a hard memory cap.
