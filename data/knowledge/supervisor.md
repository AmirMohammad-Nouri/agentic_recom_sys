# How the supervisor decides

## Monitoring loop
After every piece of feedback the supervisor compares your saved goal (stated weights) with the observed weights from your behaviour, criterion by criterion: gap = observed - stated. While every gap is below 0.10, or fewer than 3 informative events have happened since you saved the goal, it stays quiet and recommends.

## Ask or propose
With a gap of at least 0.10 after 3 or more events it asks one trade-off question ("when healthiness and convenience pull in different directions, which usually wins?"). Your answer counts as evidence but does not edit the goal. With a gap of at least 0.15 after 6 or more events it searches your history in the knowledge graph, prepares a goal proposal that moves the weight halfway toward the observed value, and shows a counterfactual of which recipes would enter or leave your top five. Nothing changes until you accept.

## Taste conflicts
If you declared a cuisine or ingredient as a liking but rejected several dishes with it, the supervisor asks whether to keep it. Removing it is only done if you choose that option.

## Restraint
The supervisor does not ask again about a topic for 6 feedback events after you answered or dismissed it. It never changes a mandatory rule; profile changes from chat are prepared and wait for your confirmation.

## The language model
The local model only classifies messages into fixed options, picks an optional follow-up tool from a fixed list, and rewrites answers from recorded facts. Rewritten answers are checked: any number or recipe name not present in the evidence, or a medical claim, causes the template answer to be used instead.
