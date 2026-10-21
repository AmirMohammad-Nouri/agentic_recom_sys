"""Builds the SYNTHETIC fixture catalogue used by the demo.

Everything here is illustrative. Ingredient nutrient values are rounded approximations
of typical food-composition values; prices and CO2e values are rounded PLACEHOLDERS
(provenance "fixture_placeholder"). They exist so the pipeline can be exercised end to
end and must not be cited or used for results. Replace them through a real catalogue
loader (see README "Using real data") before reporting anything.

Recipe-level values are computed from ingredient amounts so the fixture is internally
consistent. A few ingredients deliberately have unknown fields so that missing-value
handling is exercised.

Run:  python data/build_fixture.py
"""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).parent / "fixture"
PLACEHOLDER = "fixture_placeholder"

# name: (category, kcal, sugars_g, satfat_g, sodium_mg, fibre_g, protein_g, fv_share,
#        price_eur_per_kg, co2e_kg_per_kg, allergens)  all nutrients per 100 g
# allergens None = unknown (not the same as "none").
I = {
    "tomato": ("vegetable", 18, 2.6, 0.03, 5, 1.2, 0.9, 1, 2.5, 2.1, []),
    "onion": ("vegetable", 40, 4.2, 0.04, 4, 1.7, 1.1, 1, 1.2, 0.5, []),
    "garlic": ("vegetable", 149, 1.0, 0.09, 17, 2.1, 6.4, 1, 6.0, 0.5, []),
    "carrot": ("vegetable", 41, 4.7, 0.04, 69, 2.8, 0.9, 1, 1.2, 0.4, []),
    "spinach": ("vegetable", 23, 0.4, 0.06, 79, 2.2, 2.9, 1, 6.0, 0.6, []),
    "broccoli": ("vegetable", 34, 1.7, 0.04, 33, 2.6, 2.8, 1, 3.0, 0.9, []),
    "bell pepper": ("vegetable", 31, 4.2, 0.06, 4, 2.1, 1.0, 1, 3.5, 1.1, []),
    "mushroom": ("vegetable", 22, 2.0, 0.06, 5, 1.0, 3.1, 1, 5.0, 1.0, []),
    "zucchini": ("vegetable", 17, 2.5, 0.08, 8, 1.0, 1.2, 1, 2.5, 0.6, []),
    "eggplant": ("vegetable", 25, 3.5, 0.03, 2, 3.0, 1.0, 1, 3.0, 0.8, []),
    "potato": ("vegetable", 77, 0.8, 0.03, 6, 2.2, 2.0, 1, 1.1, 0.46, []),
    "sweet potato": ("vegetable", 86, 4.2, 0.02, 55, 3.0, 1.6, 1, 2.2, 0.5, []),
    "cabbage": ("vegetable", 25, 3.2, 0.03, 18, 2.5, 1.3, 1, 1.3, 0.5, []),
    "cucumber": ("vegetable", 15, 1.7, 0.04, 2, 0.5, 0.7, 1, 1.8, 0.6, []),
    "corn": ("vegetable", 86, 6.3, 0.2, 15, 2.0, 3.3, 1, 2.0, 0.9, []),
    "peas": ("vegetable", 81, 5.7, 0.07, 5, 5.1, 5.4, 1, 2.5, 1.0, []),
    "avocado": ("fruit", 160, 0.7, 2.1, 7, 6.7, 2.0, 1, 6.0, 2.5, []),
    "lemon": ("fruit", 29, 2.5, 0.04, 2, 2.8, 1.1, 1, 3.0, 0.7, []),
    "banana": ("fruit", 89, 12.2, 0.1, 1, 2.6, 1.1, 1, 1.6, 0.9, []),
    "berries": ("fruit", 45, 7.5, 0.02, 1, 3.0, 0.8, 1, 9.0, 1.5, []),
    "olives": ("vegetable", 115, 0.0, 1.5, 1556, 3.2, 0.8, 1, 8.0, 3.0, []),
    "tomato passata": ("vegetable", 32, 4.5, 0.05, 10, 1.5, 1.5, 1, 1.8, 1.0, []),
    "chickpeas": ("legume", 164, 4.8, 0.27, 7, 7.6, 8.9, 1, 2.2, 0.8, []),
    "lentils": ("legume", 116, 1.8, 0.05, 2, 7.9, 9.0, 1, 2.0, 0.9, []),
    "black beans": ("legume", 132, 0.3, 0.09, 1, 8.7, 8.9, 1, 2.3, 0.9, []),
    "tofu": ("soy_product", 76, 0.6, 0.7, 7, 0.3, 8.1, 0, 7.0, 3.0, ["soy"]),
    "miso": ("soy_product", 199, 6.2, 1.2, 3728, 5.4, 12.0, 0, 12.0, 1.5, ["soy"]),
    "soy sauce": ("sauce_spice", 53, 0.4, 0.0, 5493, 0.8, 8.1, 0, 5.0, 2.0, ["soy", "gluten"]),
    "white rice": ("grain", 130, 0.1, 0.08, 1, 0.4, 2.7, 0, 2.0, 1.7, []),
    "brown rice": ("grain", 123, 0.4, 0.2, 4, 1.8, 2.7, 0, 2.4, 1.7, []),
    "pasta": ("grain", 158, 0.6, 0.2, 6, 1.8, 5.8, 0, 1.6, 0.7, ["gluten"]),
    "whole wheat pasta": ("grain", 149, 0.8, 0.2, 4, 3.9, 6.0, 0, 2.0, 0.7, ["gluten"]),
    "wheat noodles": ("grain", 138, 0.6, 0.2, 5, 1.2, 4.5, 0, 3.0, 0.7, ["gluten"]),
    "rice noodles": ("grain", 108, 0.0, 0.03, 4, 0.9, 0.9, 0, 4.0, 1.0, []),
    "bread": ("grain", 265, 5.0, 0.7, 491, 2.7, 9.0, 0, 3.0, 1.6, ["gluten"]),
    "tortilla": ("grain", 304, 3.0, 1.3, 600, 3.5, 8.0, 0, 4.0, 1.6, ["gluten"]),
    "couscous": ("grain", 112, 0.1, 0.05, 5, 1.4, 3.8, 0, 2.8, 0.7, ["gluten"]),
    "quinoa": ("grain", 120, 0.9, 0.2, 7, 2.8, 4.4, 0, 5.0, 1.2, []),
    "oats": ("grain", 389, 1.0, 1.2, 2, 10.6, 16.9, 0, 2.5, 2.5, ["gluten"]),
    "chicken breast": ("meat", 165, 0.0, 1.0, 74, 0.0, 31.0, 0, 9.0, 9.9, []),
    "beef": ("meat", 215, 0.0, 7.0, 75, 0.0, 26.0, 0, 12.0, 60.0, []),
    "pork": ("meat", 242, 0.0, 5.5, 62, 0.0, 27.0, 0, 8.0, 12.3, []),
    "lamb": ("meat", 258, 0.0, 8.5, 72, 0.0, 25.0, 0, 16.0, 39.7, []),
    "salmon": ("fish", 208, 0.0, 3.1, 59, 0.0, 20.0, 0, 20.0, 13.6, ["fish"]),
    "cod": ("fish", 82, 0.0, 0.1, 54, 0.0, 18.0, 0, 18.0, 6.0, ["fish"]),
    "tuna": ("fish", 116, 0.0, 0.2, 247, 0.0, 26.0, 0, 15.0, 6.0, ["fish"]),
    "shrimp": ("shellfish", 99, 0.0, 0.2, 111, 0.0, 24.0, 0, 22.0, 26.9, ["crustaceans"]),
    "egg": ("egg", 143, 0.4, 3.1, 142, 0.0, 12.6, 0, 4.0, 4.7, ["eggs"]),
    "feta": ("dairy", 264, 4.0, 15.0, 917, 0.0, 14.0, 0, 12.0, 20.0, ["milk"]),
    "mozzarella": ("dairy", 280, 1.0, 13.0, 627, 0.0, 28.0, 0, 10.0, 24.0, ["milk"]),
    "parmesan": ("dairy", 431, 0.9, 19.0, 1529, 0.0, 38.0, 0, 20.0, 24.0, ["milk"]),
    "yogurt": ("dairy", 61, 4.7, 2.1, 46, 0.0, 3.5, 0, 2.5, 2.5, ["milk"]),
    "butter": ("dairy", 717, 0.1, 51.0, 11, 0.0, 0.9, 0, 9.0, 12.0, ["milk"]),
    "coconut milk": ("oil_fat", 197, 2.8, 17.0, 13, 0.0, 2.0, 0, 3.5, 2.0, []),
    "olive oil": ("oil_fat", 884, 0.0, 14.0, 2, 0.0, 0.0, 0, 9.0, 5.4, []),
    "peanuts": ("nut", 567, 4.7, 7.0, 18, 8.5, 26.0, 1, 6.0, 2.0, ["peanuts"]),
    "almonds": ("nut", 579, 4.4, 3.8, 1, 12.5, 21.0, 1, 14.0, 0.5, ["nuts"]),
    "tahini": ("nut", 595, 0.5, 7.5, 115, 9.3, 17.0, 0, 12.0, 2.0, ["sesame"]),
    "pesto": ("sauce_spice", 460, 3.0, 5.0, 700, 2.0, 5.0, 0, 14.0, 5.0, ["milk", "nuts"]),
    "curry paste": ("sauce_spice", 150, 8.0, 4.0, 2500, 4.0, 3.0, 0, None, 2.5, []),
    "spice mix": ("sauce_spice", 300, 3.0, 1.0, 100, 25.0, 12.0, 0, 40.0, 3.0, []),
    "sugar": ("sauce_spice", 387, 100.0, 0.0, 1, 0.0, 0.0, 0, 1.2, 0.9, []),
    "salt": ("sauce_spice", 0, 0.0, 0.0, 38758, 0.0, 0.0, 0, 0.8, 0.2, []),
    "kimchi": ("ferment", 15, 1.1, 0.03, 498, 1.6, 1.1, 1, 8.0, None, None),
}
FIELDS = ["category", "kcal", "sugars_g", "sat_fat_g", "sodium_mg", "fibre_g", "protein_g",
          "fv_share", "price_eur_per_kg", "co2e_kg_per_kg", "allergens"]

# (title, cuisine, meal, minutes, [(ingredient, grams)])
R = [
    ("Spaghetti Pomodoro", "Italian", "main", 25, [("pasta", 250), ("tomato passata", 150), ("garlic", 5), ("olive oil", 10), ("parmesan", 15), ("salt", 2)]),
    ("Pasta alla Norma", "Italian", "main", 35, [("pasta", 230), ("eggplant", 150), ("tomato passata", 120), ("mozzarella", 30), ("olive oil", 12), ("garlic", 5), ("salt", 2)]),
    ("Whole-wheat Pasta Primavera", "Italian", "main", 30, [("whole wheat pasta", 230), ("zucchini", 100), ("broccoli", 80), ("bell pepper", 60), ("olive oil", 10), ("parmesan", 10), ("garlic", 5), ("salt", 2)]),
    ("Quick Margherita Flatbread", "Italian", "main", 20, [("tortilla", 100), ("tomato passata", 80), ("mozzarella", 80), ("tomato", 60), ("olive oil", 8)]),
    ("Chicken Cacciatore with Pasta", "Italian", "main", 45, [("chicken breast", 150), ("tomato passata", 150), ("bell pepper", 80), ("onion", 50), ("olives", 20), ("pasta", 150), ("olive oil", 10), ("garlic", 5), ("salt", 2)]),
    ("Pesto Pasta with Peas", "Italian", "main", 20, [("pasta", 250), ("pesto", 40), ("peas", 80), ("parmesan", 10), ("salt", 1)]),
    ("Salmon Teriyaki Bowl", "Japanese", "main", 30, [("salmon", 140), ("white rice", 220), ("broccoli", 80), ("soy sauce", 15), ("sugar", 8), ("carrot", 40)]),
    ("Chicken Yakitori Rice Bowl", "Japanese", "main", 30, [("chicken breast", 140), ("white rice", 220), ("cucumber", 50), ("soy sauce", 15), ("sugar", 5)]),
    ("Miso Noodle Soup with Tofu", "Japanese", "main", 25, [("wheat noodles", 200), ("tofu", 100), ("miso", 25), ("mushroom", 60), ("spinach", 40)]),
    ("Vegetable Tofu Donburi", "Japanese", "main", 25, [("tofu", 140), ("white rice", 220), ("onion", 50), ("egg", 50), ("soy sauce", 15), ("carrot", 40), ("sugar", 5)]),
    ("Bibimbap", "Korean", "main", 35, [("white rice", 220), ("beef", 80), ("spinach", 60), ("carrot", 50), ("egg", 55), ("zucchini", 50), ("soy sauce", 10), ("kimchi", 40)]),
    ("Tofu Kimchi Stew", "Korean", "main", 30, [("tofu", 150), ("kimchi", 120), ("onion", 40), ("mushroom", 50), ("white rice", 180)]),
    ("Glass-style Noodle Stir-fry", "Korean", "main", 30, [("rice noodles", 220), ("spinach", 60), ("carrot", 50), ("mushroom", 50), ("soy sauce", 15), ("sugar", 6), ("olive oil", 8)]),
    ("Chana Masala with Rice", "Indian", "main", 35, [("chickpeas", 250), ("tomato passata", 120), ("onion", 60), ("garlic", 6), ("spice mix", 6), ("olive oil", 10), ("white rice", 150), ("salt", 2)]),
    ("Dal Tadka", "Indian", "main", 35, [("lentils", 250), ("tomato", 60), ("onion", 50), ("garlic", 6), ("spice mix", 5), ("olive oil", 10), ("white rice", 150), ("salt", 2)]),
    ("Butter Chicken with Rice", "Indian", "main", 45, [("chicken breast", 150), ("tomato passata", 120), ("yogurt", 50), ("butter", 15), ("onion", 40), ("spice mix", 6), ("white rice", 180), ("salt", 2)]),
    ("Saag Tofu", "Indian", "main", 35, [("spinach", 200), ("tofu", 130), ("onion", 50), ("garlic", 6), ("spice mix", 5), ("coconut milk", 40), ("white rice", 150), ("salt", 2)]),
    ("Aloo Gobi-style Potatoes", "Indian", "main", 40, [("potato", 220), ("broccoli", 120), ("onion", 40), ("spice mix", 6), ("olive oil", 12), ("salt", 2)]),
    ("Black Bean Burrito Bowl", "Mexican", "main", 25, [("black beans", 180), ("white rice", 160), ("corn", 60), ("tomato", 60), ("avocado", 50), ("spice mix", 4), ("salt", 1)]),
    ("Chicken Fajitas", "Mexican", "main", 30, [("chicken breast", 150), ("bell pepper", 100), ("onion", 60), ("tortilla", 100), ("avocado", 40), ("spice mix", 4), ("olive oil", 8)]),
    ("Beef Tacos", "Mexican", "main", 30, [("beef", 120), ("tortilla", 110), ("tomato", 60), ("mozzarella", 30), ("spice mix", 4), ("cabbage", 40)]),
    ("Veggie Quesadilla", "Mexican", "main", 20, [("tortilla", 110), ("black beans", 100), ("bell pepper", 60), ("mozzarella", 50), ("corn", 40), ("spice mix", 3)]),
    ("Greek Salad with Chickpeas", "Mediterranean", "main", 15, [("tomato", 120), ("cucumber", 100), ("feta", 50), ("olives", 30), ("chickpeas", 100), ("onion", 30), ("olive oil", 15)]),
    ("Grilled Salmon with Couscous", "Mediterranean", "main", 30, [("salmon", 140), ("couscous", 180), ("zucchini", 100), ("lemon", 10), ("olive oil", 12), ("salt", 1.5)]),
    ("Shakshuka with Bread", "Mediterranean", "main", 30, [("egg", 110), ("tomato passata", 200), ("bell pepper", 80), ("onion", 50), ("spice mix", 4), ("olive oil", 10), ("bread", 60), ("salt", 2)]),
    ("Hummus and Veggie Plate", "Mediterranean", "main", 15, [("chickpeas", 150), ("tahini", 30), ("carrot", 80), ("cucumber", 80), ("bell pepper", 60), ("bread", 60), ("olive oil", 10), ("lemon", 10), ("salt", 1)]),
    ("Lemon Chicken with Quinoa", "Mediterranean", "main", 30, [("chicken breast", 150), ("quinoa", 180), ("broccoli", 100), ("lemon", 10), ("olive oil", 10), ("salt", 1.5)]),
    ("Baked Salmon with Potatoes", "Nordic", "main", 40, [("salmon", 140), ("potato", 250), ("broccoli", 80), ("butter", 10), ("salt", 1.5)]),
    ("Creamy Mushroom Potatoes", "Nordic", "main", 35, [("potato", 250), ("mushroom", 120), ("onion", 40), ("yogurt", 60), ("butter", 10), ("salt", 1.5)]),
    ("Meatballs with Potatoes", "Nordic", "main", 50, [("beef", 100), ("pork", 50), ("potato", 250), ("bread", 20), ("egg", 20), ("yogurt", 40), ("onion", 30), ("butter", 10), ("salt", 2), ("cabbage", 60)]),
    ("Oat Porridge with Berries", "Nordic", "breakfast", 10, [("oats", 80), ("berries", 80), ("yogurt", 100), ("banana", 60)]),
    ("Kung Pao Chicken with Rice", "Chinese", "main", 30, [("chicken breast", 140), ("peanuts", 30), ("bell pepper", 80), ("white rice", 200), ("soy sauce", 15), ("sugar", 6), ("garlic", 5), ("olive oil", 10)]),
    ("Beef and Broccoli", "Chinese", "main", 25, [("beef", 120), ("broccoli", 150), ("white rice", 200), ("soy sauce", 15), ("sugar", 5), ("garlic", 5), ("olive oil", 10)]),
    ("Mapo Tofu", "Chinese", "main", 30, [("tofu", 200), ("pork", 60), ("white rice", 200), ("spice mix", 4), ("soy sauce", 12), ("garlic", 5), ("olive oil", 10)]),
    ("Vegetable Fried Rice", "Chinese", "main", 20, [("white rice", 250), ("egg", 60), ("peas", 50), ("carrot", 50), ("onion", 30), ("soy sauce", 15), ("olive oil", 10)]),
    ("Green Curry with Tofu", "Thai", "main", 35, [("tofu", 150), ("curry paste", 25), ("coconut milk", 120), ("zucchini", 80), ("bell pepper", 60), ("white rice", 180)]),
    ("Shrimp Pad Thai", "Thai", "main", 30, [("rice noodles", 220), ("shrimp", 100), ("egg", 50), ("peanuts", 25), ("sugar", 8), ("soy sauce", 12), ("lemon", 10), ("olive oil", 10)]),
    ("Thai Basil Chicken", "Thai", "main", 25, [("chicken breast", 150), ("white rice", 220), ("bell pepper", 60), ("garlic", 6), ("soy sauce", 12), ("sugar", 5), ("olive oil", 10)]),
    ("Persian Herb and Lentil Stew", "Persian", "main", 60, [("lentils", 220), ("spinach", 120), ("onion", 50), ("white rice", 150), ("olive oil", 10), ("salt", 2)]),
    ("Chicken Joojeh with Rice", "Persian", "main", 45, [("chicken breast", 160), ("white rice", 220), ("tomato", 80), ("onion", 40), ("yogurt", 40), ("olive oil", 10), ("salt", 2)]),
    ("Kuku Sabzi Herb Frittata", "Persian", "main", 30, [("egg", 150), ("spinach", 100), ("onion", 30), ("olive oil", 12), ("bread", 60), ("yogurt", 40), ("salt", 2)]),
    ("Lamb Kofta with Couscous", "Middle Eastern", "main", 35, [("lamb", 130), ("couscous", 180), ("tomato", 80), ("cucumber", 60), ("yogurt", 40), ("spice mix", 4), ("salt", 1.5)]),
    ("Falafel Wrap", "Middle Eastern", "main", 30, [("chickpeas", 150), ("tortilla", 100), ("tahini", 20), ("tomato", 60), ("cucumber", 50), ("cabbage", 40), ("olive oil", 15), ("spice mix", 3), ("salt", 1.5)]),
    ("Beef Burger with Potato Wedges", "American", "main", 30, [("beef", 130), ("bread", 90), ("potato", 150), ("tomato", 40), ("mozzarella", 20), ("olive oil", 15), ("salt", 2)]),
    ("Black Bean Veggie Burger", "American", "main", 35, [("black beans", 120), ("oats", 30), ("bread", 90), ("avocado", 40), ("tomato", 40), ("potato", 150), ("olive oil", 12), ("salt", 2)]),
    ("Chicken Caesar-style Salad", "American", "main", 20, [("chicken breast", 130), ("cabbage", 100), ("parmesan", 20), ("bread", 30), ("olive oil", 15), ("egg", 20), ("salt", 1)]),
    ("Tuna Sandwich", "American", "main", 10, [("tuna", 100), ("bread", 100), ("cucumber", 40), ("tomato", 40), ("yogurt", 20), ("salt", 0.5)]),
    ("Loaded Sweet Potato", "American", "main", 40, [("sweet potato", 300), ("black beans", 100), ("avocado", 40), ("yogurt", 40), ("corn", 40), ("spice mix", 3)]),
    ("Yogurt Berry Parfait", "American", "breakfast", 5, [("yogurt", 200), ("berries", 80), ("oats", 40), ("banana", 60)]),
    ("Hearty Lentil Soup", "Mediterranean", "main", 40, [("lentils", 200), ("carrot", 70), ("onion", 50), ("tomato", 60), ("olive oil", 10), ("spice mix", 3), ("salt", 2), ("bread", 40)]),
    ("Cod with Roasted Vegetables", "Nordic", "main", 40, [("cod", 150), ("potato", 200), ("zucchini", 100), ("carrot", 60), ("olive oil", 12), ("lemon", 10), ("salt", 1.5)]),
    ("Mushroom Risotto", "Italian", "main", 40, [("white rice", 220), ("mushroom", 120), ("onion", 40), ("parmesan", 25), ("butter", 12), ("olive oil", 8), ("salt", 2)]),
    ("Vegan Buddha Bowl", "Mediterranean", "main", 35, [("quinoa", 150), ("chickpeas", 100), ("sweet potato", 120), ("spinach", 50), ("avocado", 40), ("tahini", 15), ("salt", 1)]),
    ("Peanut Noodle Salad", "Chinese", "main", 20, [("rice noodles", 200), ("peanuts", 25), ("carrot", 50), ("cucumber", 50), ("cabbage", 40), ("soy sauce", 12), ("sugar", 5)]),
]

ANIMAL = {"meat", "fish", "shellfish", "egg", "dairy"}
MEAT = {"meat"}
FISHY = {"fish", "shellfish"}
ALL_ALLERGENS = ["gluten", "crustaceans", "eggs", "fish", "peanuts", "soy", "milk", "nuts",
                 "celery", "mustard", "sesame", "sulphites", "lupin", "molluscs"]


def ingredient_record(name):
    return dict(zip(FIELDS, I[name]), name=name)


def tri(ok_known, unknown):
    if unknown:
        return None
    return bool(ok_known)


def build_recipe(idx, title, cuisine, meal, minutes, items):
    total = float(sum(g for _, g in items))
    recs = [(ingredient_record(n), g) for n, g in items]

    def per100(key):
        return sum(r[key] * g for r, g in recs) / total

    fv = sum(r["fv_share"] * g for r, g in recs) / total * 100.0
    unknown_allergen_ings = [r["name"] for r, _ in recs if r["allergens"] is None]
    allergens = sorted({a for r, _ in recs if r["allergens"] for a in r["allergens"]})
    cats = {r["category"] for r, _ in recs}
    animal_unknown = any(r["name"] == "kimchi" for r, _ in recs)

    cost_missing = [r["name"] for r, _ in recs if r["price_eur_per_kg"] is None]
    co2_missing = [r["name"] for r, _ in recs if r["co2e_kg_per_kg"] is None]
    cost = None if cost_missing else sum(r["price_eur_per_kg"] * g / 1000.0 for r, g in recs)
    co2 = None if co2_missing else sum(r["co2e_kg_per_kg"] * g / 1000.0 for r, g in recs)

    has_meat = bool(cats & MEAT)
    has_fish = bool(cats & FISHY)
    has_animal = bool(cats & ANIMAL)
    allergens_complete = not unknown_allergen_ings
    missing = []
    if cost is None:
        missing.append("cost")
    if co2 is None:
        missing.append("sustainability")
    if not allergens_complete:
        missing.append("allergens")
    if animal_unknown:
        missing.append("animal_origin")

    return {
        "id": f"r{idx:03d}",
        "title": title,
        "cuisine": cuisine,
        "meal": meal,
        "servings": 1,
        "serving_grams": round(total, 1),
        "prep_minutes": minutes,
        "ingredients": [
            {"name": r["name"], "grams": g, "category": r["category"],
             "allergens": r["allergens"]} for r, g in recs
        ],
        "allergens": allergens,
        "allergens_complete": allergens_complete,
        "unknown_allergen_ingredients": unknown_allergen_ings,
        "diet": {
            "vegetarian": tri(not has_meat and not has_fish, animal_unknown),
            "vegan": tri(not has_animal, animal_unknown),
            "pescatarian": not has_meat,
            "gluten_free": tri("gluten" not in allergens, not allergens_complete),
            "dairy_free": tri("milk" not in allergens, not allergens_complete),
        },
        "nutrition_per_100g": {
            "energy_kj": round(per100("kcal") * 4.184, 1),
            "sugars_g": round(per100("sugars_g"), 2),
            "sat_fat_g": round(per100("sat_fat_g"), 2),
            "sodium_mg": round(per100("sodium_mg"), 1),
            "fibre_g": round(per100("fibre_g"), 2),
            "protein_g": round(per100("protein_g"), 2),
            "fruit_veg_nuts_pct": round(fv, 1),
        },
        "kcal_per_serving": round(per100("kcal") * total / 100.0),
        "cost_eur_per_serving": None if cost is None else round(cost, 2),
        "cost_basis": "per serving, ingredient price x grams",
        "cost_provenance": PLACEHOLDER,
        "co2e_kg_per_serving": None if co2 is None else round(co2, 3),
        "co2e_provenance": PLACEHOLDER,
        "missing": missing,
        "source": "synthetic_fixture_v1",
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    recipes = [build_recipe(i + 1, *r) for i, r in enumerate(R)]
    payload = {
        "name": "synthetic_fixture",
        "note": ("SYNTHETIC. Nutrient values approximate; prices and CO2e are rounded "
                 "placeholders. Not for citation or reporting."),
        "recipes": recipes,
    }
    text = json.dumps(payload, indent=1, sort_keys=True)
    (OUT / "recipes.json").write_text(text)
    version = hashlib.sha256(text.encode()).hexdigest()[:12]
    (OUT / "VERSION").write_text(version + "\n")
    ing = {n: ingredient_record(n) for n in I}
    (OUT / "ingredients.json").write_text(json.dumps(ing, indent=1, sort_keys=True))
    print(f"{len(recipes)} recipes, catalogue_version={version}")
    miss = {}
    for r in recipes:
        for m in r["missing"]:
            miss[m] = miss.get(m, 0) + 1
    print("missingness:", miss)


if __name__ == "__main__":
    main()
