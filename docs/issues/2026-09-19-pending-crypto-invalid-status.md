# Pending crypto contract returns HTTP error rather than empty candles

The first latest-data run for 2026-09-18 stopped on GAIBUSDT. Exchange metadata said PENDING_TRADING, but `/fapi/v1/klines` returned HTTP 400 with code -1122 (`Invalid symbol status.`), not the successful empty list used by the frozen test fixture. Quant's existing retry helper discarded the error body and returned None; the new scanner correctly stopped rather than treating that generic failure as zero volume.

Verified current metadata, raw error body and official archive listing. The handling now re-inspects an error body only for a metadata-confirmed pending symbol. An explicit -1122 becomes a distinct exception. The collector excludes it only when the metadata also says PENDING_TRADING and the archive query succeeds with no period trading files. A trading contract, an archived historical trade, a transport error, or another exchange code still blocks publication.

Regression: four new cases reproduced RED; local relevant suite 127 passed. Changes and latest-data run use the existing isolated branch/cloud temporary test directory; production files and Telegram are untouched. Raw evidence is retained with the 2026-09-19 latest-data artifacts. Official daily archives lag: lack of a file alone is not a sufficient exclusion reason.
