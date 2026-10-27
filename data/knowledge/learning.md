# How the system learns your preferences

## Taste model
For every cuisine, ingredient and food group the system counts likes and dislikes. taste = (1 + likes) / (2 + likes + dislikes), so 0.5 means no opinion. A like or a taste rejection counts 1 for every concept of the dish, a choice 0.8, a save 0.6. Cuisines and ingredients you list at onboarding start with 2 likes (or 2 dislikes).

## Value model: stated versus observed
Your saved goal holds the stated weights for healthiness, sustainability and convenience. The observed weights start equal to them and move with your behaviour. When you choose a dish from the five shown, each observed weight is multiplied by exp(d), where d is how much better the chosen dish scores on that criterion than the average of the five shown; then the weights are rescaled to sum to 1 and 5 % of the stated goal is mixed back in. A reason chip (health, climate, easy) adds a small extra push to that criterion, a reasoned rejection (not healthy enough, not sustainable, too complicated) works the same way, and answering a trade-off question pushes the winner up and the loser down. Saving a new goal moves the observed weights halfway to it.

## Preference match
Preference match = 1 - half of the sum of |stated - observed| over the three criteria. 100 % means your choices behave exactly like your goal. Confidence = n / (n + 5), where n is the number of informative events since the goal was last saved.

## The goal never changes by itself
Observed weights are an estimate. The system never rewrites your goal; the supervisor can only ask a question or propose a change that you accept or dismiss.

## Corrections
You can override any taste belief from the Insights page. A correction replaces the belief and is recorded as your correction.
