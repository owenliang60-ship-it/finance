# Floating percentile sums can break mathematical score ties

The latest 7-day report gave ARB and 龙虾 mathematically equal 80.33898305084746 scores, but separately summed percentile floats differed by 1e-14 and overrode the documented symbol tiebreak. A three-member regression reproduced scores 60.0 vs 60.000000000000014 for equivalent weighted ranks.

Compute the 40/20/20/20 integer-point weighted count numerator first, then divide once by valid pool size. This preserves equivalent score ties before sorting by symbol and does not round distinct scores together. The reproducing test failed before the fix and passed after it. Latest reports are reranked from the same captured prices with no new market calls; Top10 members unchanged, 7-day positions 8/9 become ARB/龙虾.
