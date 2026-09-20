# Crypto v3 turnover30 — deployed and delivered

User explicitly authorized deploying the previously designed30% absolute-turnover scoring and sending latest10/14daily rankings. Runtime04913597 is merged/pushed and deployed at aliyun:/root/workspace/Finance. Telegram messages1843(10day) and1844(14day) both confirmed APIok, exact text SHA256 and configured destination match. Data cutoff2026-09-20 08:00Beijing /00UTC. Daily08:06schedule unchanged.

## Scoring and scope

v3 weights: mathematicalabs(return)35%,ER15%,R²15%,currentdrawdown5%,mean dailyUSDTturnover30%. Firstfour use existingcount<=/validNpercentiles; turnover uses100V/(V+1bnUSDT/day), not percentile. Newrowturnover_score and schema4metadata expose the formula. BTC remains excluded from candidate and scoring pools. v1/v2 preserved for comparisons; weekly report scoring and Quant other scanners unchanged. No funding included in research, no real trades submitted.

## Evidence

- Six v3 tests observedRED, then targeted260PASS local,260PASS cloudstage,260PASS production. Research333PASS includingv3HoldingPolicy direction/score-column check. Syntax and diffcheckPASS; main-threadreview no unresolved findings.
- Full local3832PASS/12pre-existingFAIL/4SKIP,281.48seconds. Same7breadth+5morning-report failureIDs as prior rollouts.
- Independent frozen-source preview reconstruction verifies14dailyTop100sets,2pools and163validcoin-windowmetrics/scores/orderings. Production replay identical except generated_at timestamp. Bothtelegrammessages exactmatches validatedpreview; prior publishedsame-datefiles archived before replacement.
- Backtest wraps existingexecution/attribution/independentverifier. Fivepaths coverv3IS/OOS/full,exBTCv2OOScontrol andv3noFisher50/50OOS.92011valid14dayfactors independentlyrescoredmaxerror2.84e-14;20177NAVobservations/9592trades independentlyverifiedfromraw4hprices; noBTCtrades/fundingcharges; originaldatahashes unchanged. Researchcommit0fc23e62.
- Newv3FisherweeklyOOS+433.18% (long+94.15pp,short+339.03pp),MDD60.25%;IS−84.89%;continuousfull−19.11%,MDD94.63%. SameexBTCv2OOS+888.91%. This is a user-prescribedliquiditypreference, notvalidatedreturnimprovement; no blindOOSclaim.

## Rollback and artifacts

Cloud backup/root/workspace/Quant/backups/crypto-volume30-20260920 contains priorruntime,oldHEAD,cron/entrypoint/weeklyhashsnapshots,validatedpreview,prior-artifacts,productiontests,deployed.json,telegram-receipts.json. SharedQuantlock held for staging/deploy/send. No restart/crontabedit. Runtime rollback is reviewedrevert04913597; never resend on uncertain receipt.

Local reports/crypto_daily_rankings/2026-09-20-volume30/ and reports/crypto-fisher-2026-09-20/volume30-results/report.md. Reproductionwrapperrun_study.py and resultCSV/NAV/ledger/sideattribution/manifests retained. Nextnaturalcronrun has notyetoccurred; no newmonitorautomationcreated.
