# Crypto weekly report — implementation acceptance, rollout blocked

Branch codex/crypto-weekly-report, baseline7bbdad6. Two DSH implementation sessions (default DeepSeek-V41-Flash) reached40-step limits; Codex completed review/corrections/documentation and directlyverified outputs. No subagents were spawned.

- Focused command: `python -m pytest tests/test_crypto*.py tests/test_fisher_indicator.py tests/test_rvol_sustained.py -q` →211passed in5.84s.
- Full suite →3781passed,12failed,4skipped. All12failureIDs match reports/crypto-alpha-2026-09-19/report/verification_logs/crypto-alpha-full-validated.log. Unchanged7breadthsnapshot failures and5morningconcept-render failures. Final catalog chronological-order correction covered by additional focusedtests.
- An interrupt during an unrelated slowcurl callback produced a warning; affectedtest test_pipeline_scratchpad::test_collect_data_without_scratchpad reran separately →1passed in4.48s. Do not describe fullsuite as green.
- LocalPython3.10 grammar, actualcloudPython3.10 compile, bash-n andgit diff--check pass. Newwrapperexecutable.
- Independentstdlib calculation from frozen daily evidence plus live missingdailybars selects latest-week100; native1w API inputs independently reproduce RVOL fromdailyaggregation and Fisherrecursion. Cutoff2026-09-14: RVOLvalid77, Fishervalid96, newup2/down5 (2%/5% offixed100). JSON andindicatorpreview under reports/crypto_weekly/2026-09-20-validation/.
- Strict cloud dryrun failed asintended at historiccatalog gate: AERGOUSDT,BDXNUSDT,BTCSTUSDT,SXPUSDT. Source coverageNOTpassed; nofulltrendpreview/Telegramsend.
- Userclarification pending on allowing explicit invariant-pool evidence for the AERGOterminalday gap. Preliminaryindependentproof at thiscutoff finds78/73/59members in7/14/30weekpools identical under bothboundarycases, but that isnot sourcecomplete, notblanket futureproof, and notimplemented asfallback.

Production unchanged: no merge/push, no croninstall, noexisting dailyfiles modified. Remotevalidation isolated at /tmp/crypto-weekly-validation-20260920. Localprivatetaskbriefs, raw1winputs, referencecode, fulllogs in work/crypto-weekly/. No credentials copied or committed.

Remaining: resolvehistoricalmetadata/terminalturnoverpolicy, implement/test permitted approach, complete real report andindependentpool/score audit, then rollout under sharedQuantlock withcrontab backup andverification.

## 2026-09-20 addendum — current-universe scope (history above retained)

Boss 明确「下架的就不需要了」: the weekly report is now current-tradable-only (`CurrentTrendMarket`/`current_catalog`), excluding delisted/SETTLING/PENDING/historical-only contracts before any history fetch. The historical AERGO/BDXN coverage gate above is superseded for the weekly report; the acceptance evidence remains dated history. Daily historical-universe behavior is unchanged. Live smoke and deployment remain NOT done.

## Current-only acceptance, 2026-09-20

DSH third invocation implemented the user-approved current-only universe and completed successfully. Codex reviewed the finaldiff, verified217focusedtests locally and217oncloudPython3.10. Real cloudrawdata:526eligiblecontracts,527prefetchrequests,zero failures; reuse100nativeweeklyhistories. Independentstdlib verifier reproduced3000weeklyTop100records,1040metricvalues, percentiles, threepools andTop10ranks. RVOLvalid77;Fishervalid96,newup2/down5. Pools7/14/30=78/73/60;valid77/72/59. WrapperrealLinuxfixture verifiedsharedlocktimeout75 andsubsequentexecution0 plusenvexport; noTelegramcredentials or sends. Evidence:reports/crypto_weekly/2026-09-20-current-only/.
