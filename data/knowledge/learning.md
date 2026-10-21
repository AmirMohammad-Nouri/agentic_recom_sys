# How the system learns your preferences

## Two models
The taste model keeps a Beta belief for each cuisine, ingredient and ingredient category. Likes, saves and choices push the belief toward 1, taste rejections toward 0, and evidence fades with a 90-day half-life. The choice model keeps a Gaussian belief over how you actually trade off the five criteria, updated from which recipe you choose among those shown, the reason you give, criterion-specific rejections such as too expensive, explicit goal statements and your answers to trade-off questions.

## Stated versus revealed
Your saved goal is what you said you want. The revealed weights are what your behaviour suggests. The system reports the difference with a probability and a 90 % interval, but it never rewrites the saved goal. Only you can save a goal or accept a proposal.

## Confidence
Confidence compares the current uncertainty with the uncertainty before any evidence: 0 means nothing learned yet, values near 1 mean the behaviour pins the weight down well.

## Corrections
You can override any taste belief from the Insights page. A correction replaces the belief with a confident one and is recorded as your correction in the provenance.
