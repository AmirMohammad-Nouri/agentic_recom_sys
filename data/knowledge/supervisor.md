# How the supervisor decides

## Monitoring loop
After every piece of feedback the supervisor compares your saved goal with your revealed behaviour. If no criterion differs with probability of at least 70 %, it stays quiet. If one does, it searches your interaction history in the knowledge graph.

## Ask or propose
With strong evidence (probability at least 90 % and at least 4 informative events since your last explicit statement) it prepares a goal proposal, moves the weight halfway toward the revealed value, and shows a counterfactual of which recipes would enter or leave your top five. With weaker evidence it asks one trade-off question instead. Your answer counts as evidence but does not edit the goal.

## Taste conflicts
If you declared a cuisine or ingredient as a liking but rejected several dishes with it, the supervisor asks whether to keep it. Removing it is only done if you choose that option.

## Restraint
The supervisor does not ask again about a topic for 6 feedback events after you answered or dismissed it. It never changes a mandatory rule; profile changes from chat are prepared and wait for your confirmation.

## The language model
The local model only classifies messages into fixed options, picks an optional follow-up tool from a fixed list, and rewrites answers from recorded facts. Rewritten answers are checked: any number or recipe name not present in the evidence, or a medical claim, causes the template answer to be used instead.
