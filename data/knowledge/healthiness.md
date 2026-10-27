# How healthiness is scored

## Nine factors
Healthiness combines nine factors from the recipe's nutrients per 100 g, each scored 0 to 1 between a good and a poor anchor (linear in between):
energy 125 to 400 kcal (WCRF energy-density bands); total fat 3 to 17.5 g, saturated fat 1.5 to 5 g, sugars 5 to 22.5 g and sodium 120 to 600 mg (UK FSA traffic-light limits); cholesterol 20 to 100 mg; fibre 6 g down to 0 g (EU high-fibre claim); protein 20 % down to 0 % of energy (EU high-protein claim); and the dataset's FSA health score (1 = healthiest).

## Health goal
Healthiness = sum(weight x factor score) / sum(weight). The weights come from the health goal chosen at onboarding. Eat healthier weighs all nine factors equally. Lose weight puts more weight on energy, fat, sugars and fibre. Build muscle puts most weight on protein. Heart-friendly eating stresses saturated fat, sodium, cholesterol and fibre. Cut down on sugar stresses sugars. Gain weight reverses the energy factor so energy-dense dishes score higher. Changing the goal changes the weights, never the data.

## Grade
The A to E health grade is FoodRec's own banding of the score (A from 0.8, B from 0.65, C from 0.5, D from 0.35); it is not the official Nutri-Score. Missing nutrients are left out and the remaining weights rescaled; the breakdown lists them.

## Not advice
Healthiness is an operational ranking measure, not medical or dietetic advice.
