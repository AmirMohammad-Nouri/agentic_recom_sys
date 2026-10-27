"""Food groups: the reference table every ingredient is mapped to.

Each group carries the values the scoring needs and where they come from:
  co2e      kg CO2e per kg of food. "P&N 2018" = Poore & Nemecek (2018), Science 360:987,
            global mean per kg of product as published by Our World in Data. Values marked
            "approx." are documented approximations for foods that study does not list.
  density   g per ml, to turn cups/spoons into grams.
  unit_g    grams for one item when the recipe gives a count ("2 eggs").
  allergens EU allergen groups the food belongs to.
  animal    "meat", "fish", "dairy", "egg", "other_animal" or None (plant / neutral).

The keyword rules at the bottom are the offline classifier. The local language model
(classify.py) picks from the same group keys, so both paths produce the same structure.
"""
import re

P_N = "Poore & Nemecek 2018 (OWID)"
APPROX = "approx."

#        key: (label, co2e, source, density, unit_g, allergens, animal)
GROUPS = {
    "beef":            ("Beef and veal", 99.48, P_N, 0.85, 150, [], "meat"),
    "lamb":            ("Lamb and goat", 39.72, P_N, 0.85, 150, [], "meat"),
    "pork":            ("Pork, bacon, ham, sausage", 12.31, P_N, 0.85, 100, [], "meat"),
    "poultry":         ("Chicken, turkey, duck", 9.87, P_N, 0.85, 150, [], "meat"),
    "meat_other":      ("Meat, unspecified", 12.31, APPROX + " (as pork)", 0.85, 150, [], "meat"),
    "fish":            ("Fish", 13.63, P_N + " farmed fish", 0.85, 150, ["fish"], "fish"),
    "crustaceans":     ("Shrimp, prawns, crab, lobster", 26.87, P_N + " farmed prawns", 0.85, 15, ["crustaceans"], "fish"),
    "molluscs":        ("Clams, mussels, squid", 13.63, APPROX + " (as farmed fish)", 0.85, 15, ["molluscs"], "fish"),
    "cheese":          ("Cheese", 23.88, P_N, 0.45, 25, ["milk"], "dairy"),
    "butter":          ("Butter and ghee", 9.25, "Clune et al. 2017 (median)", 0.96, 113, ["milk"], "dairy"),
    "cream":           ("Cream, sour cream, ice cream", 5.0, APPROX + " (milk fat share)", 1.0, 30, ["milk"], "dairy"),
    "milk":            ("Milk, buttermilk, yogurt", 3.15, P_N, 1.03, 240, ["milk"], "dairy"),
    "eggs":            ("Eggs", 4.67, P_N, 1.0, 50, ["eggs"], "egg"),
    "wheat":           ("Flour, bread, pasta, wheat products", 1.57, P_N + " wheat & rye", 0.55, 30, ["gluten"], None),
    "rice":            ("Rice and quinoa", 4.45, P_N + " rice", 0.78, 185, [], None),
    "oats":            ("Oats", 2.48, P_N + " oatmeal", 0.38, 40, ["gluten"], None),
    "maize":           ("Corn, cornmeal, corn starch, corn tortillas", 1.70, P_N, 0.6, 30, [], None),
    "potatoes":        ("Potatoes", 0.46, P_N, 0.6, 200, [], None),
    "root_veg":        ("Root vegetables (carrot, beet, sweet potato)", 0.43, P_N, 0.6, 80, [], None),
    "tomatoes":        ("Tomatoes and tomato products", 2.09, P_N, 1.0, 120, [], None),
    "onions":          ("Onions, leeks, garlic", 0.50, P_N + " onions & leeks", 0.6, 120, [], None),
    "brassicas":       ("Cabbage, broccoli, cauliflower, kale", 0.51, P_N, 0.4, 300, [], None),
    "vegetables":      ("Other vegetables and fresh herbs", 0.53, P_N + " other vegetables", 0.5, 100, [], None),
    "pulses":          ("Beans, lentils, chickpeas", 1.79, P_N + " other pulses", 0.75, 100, [], None),
    "peas":            ("Peas", 0.98, P_N, 0.6, 100, [], None),
    "soy":             ("Tofu, soy milk, soy sauce, tempeh", 3.16, P_N + " tofu", 1.0, 100, ["soy"], None),
    "citrus":          ("Citrus fruit and juice", 0.39, P_N, 1.0, 100, [], None),
    "apples":          ("Apples, applesauce, cider", 0.43, P_N, 0.6, 180, [], None),
    "bananas":         ("Bananas", 0.86, P_N, 0.6, 120, [], None),
    "berries":         ("Berries, grapes, raisins", 1.53, P_N, 0.6, 5, [], None),
    "fruit":           ("Other fruit, avocado, coconut", 1.05, P_N + " other fruit", 0.6, 150, [], None),
    "nuts":            ("Tree nuts and seeds", 0.43, P_N + " nuts", 0.55, 5, ["nuts"], None),
    "peanuts":         ("Peanuts and peanut butter", 3.23, P_N + " groundnuts", 0.6, 5, ["peanuts"], None),
    "olive_oil":       ("Olive oil", 5.42, P_N, 0.92, 15, [], None),
    "rapeseed_oil":    ("Canola, rapeseed, sunflower oil", 3.70, P_N, 0.92, 15, [], None),
    "vegetable_oil":   ("Vegetable oil, margarine, shortening", 6.32, P_N + " soybean oil", 0.92, 15, [], None),
    "sugar":           ("Sugar, syrup, honey, jam", 3.20, P_N + " cane sugar", 0.85, 12, [], None),
    "chocolate":       ("Chocolate and cocoa", 46.65, P_N + " dark chocolate", 0.7, 30, [], None),
    "coffee":          ("Coffee (brewed)", 1.70, APPROX + " (P&N coffee x 6% grounds)", 1.0, 240, [], None),
    "wine":            ("Wine, beer, spirits", 1.79, P_N + " wine", 1.0, 150, ["sulphites"], None),
    "broth":           ("Broth, stock, bouillon", 0.80, APPROX + " (mostly water)", 1.0, 5, [], None),
    "soup_mix":        ("Canned condensed soup, sauce or baking mix", 1.50, APPROX, 1.05, 300, [], None),
    "condiments":      ("Sauces and condiments (ketchup, mayo, mustard, vinegar)", 1.50, APPROX, 1.05, 15, [], None),
    "spices":          ("Salt, spices, dried herbs, leavening, extracts", 1.00, APPROX + " (tiny amounts)", 0.6, 1, [], None),
    "water":           ("Water and ice", 0.0, "none", 1.0, 240, [], None),
}
UNCLASSIFIED = "unclassified"
UNCLASSIFIED_CO2E = 1.50  # documented default; recipes report the share of mass it covers


def group(key):
    if key not in GROUPS:
        return {"key": UNCLASSIFIED, "label": "Not recognised", "co2e": UNCLASSIFIED_CO2E,
                "source": APPROX + " default", "density": 0.8, "unit_g": 50, "allergens": [], "animal": None}
    label, co2e, src, dens, unit_g, allergens, animal = GROUPS[key]
    return {"key": key, "label": label, "co2e": co2e, "source": src, "density": dens, "unit_g": unit_g,
            "allergens": list(allergens), "animal": animal}


def reference():
    """Food-group reference written next to the catalogue (used by the graph and retrieval)."""
    return {k: {**group(k), "name": k} for k in GROUPS}


# ---- offline keyword classifier -------------------------------------------------------
# Ordered: the first matching rule wins, so specific phrases come before general words.
RULES = [
    ("water", r"club soda|seltzer|^(lukewarm |warm |cold |hot |boiling |ice |iced )?water$|^ice cubes?$|^(warm |cold |hot |boiling |ice |iced )?water$|^ice( cubes)?$|quarts? water"),
    ("peanuts", r"peanut"),
    ("butter", r"(?<!peanut )(?<!apple )(?<!nut )\bbutter\b(?!milk|nut|scotch| beans)|\bghee\b"),
    ("milk", r"buttermilk|\bmilk\b(?! chocolate)|yogh?urt|kefir|\bwhey\b"),
    ("soy", r"\bsoy|tofu|tempeh|tamari|edamame|miso\b|teriyaki|hoisin"),
    ("broth", r"broth|stock\b|bouillon|consomm"),
    ("soup_mix", r"gravy mix|cream of \w+ soup|soup mix|\bsoup\b|cake mix|pudding mix|biscuit mix|bisquick|jell-?o|gelatin|stuffing mix|onion soup"),
    ("chocolate", r"chocolate|cocoa|nutella|cacao"),
    ("coffee", r"coffee|espresso"),
    ("wine", r"amaretto|\bwine\b(?! vinegar)|sherry|\bbeer\b|\brum\b|vodka|bourbon|brandy|whiske?y|tequila|liqueur|kahlua|cognac|marsala|sake\b"),
    ("condiments", r"miracle whip|liquid smoke|vinegar|ketchup|catsup|mayonnaise|\bmayo\b|mustard|worcestershire|hot sauce|tabasco|sriracha|barbecue sauce|bbq sauce|steak sauce|salad dressing|dressing|relish|pesto|pickle|horseradish|fish sauce|oyster sauce|chili sauce|picante"),
    ("cheese", r"cheese|parmesan|parmigiano|mozzarella|cheddar|feta|ricotta|provolone|gouda|brie|gruyere|romano|velveeta|mascarpone|asiago|monterey|colby"),
    ("cream", r"cream(?! of tartar)|half-and-half|half and half|cool whip|whipped topping|creme fraiche|ice cream"),
    ("eggs", r"\beggs?\b(?!plant)|yolks?|^whites?$|egg whites?|meringue"),
    ("crustaceans", r"shrimp|prawn|crab|lobster|crawfish|crayfish"),
    ("molluscs", r"clam|mussel|oyster|scallop|squid|calamari|octopus"),
    ("fish", r"fish|salmon|tuna|\bcod\b|tilapia|halibut|trout|anchov|sardine|catfish|mahi|snapper|haddock|sole\b|flounder|swordfish|mackerel|bass\b"),
    ("beef", r"rump roast|beef|steak|sirloin|chuck|brisket|hamburger|ground round|veal|meatball|roast beef|corned"),
    ("lamb", r"\blamb\b|mutton|\bgoat\b"),
    ("pork", r"pork|bacon|\bham\b|sausage|pepperoni|salami|prosciutto|chorizo|pancetta|kielbasa|hot dogs?|frankfurter|\blard\b|spareribs|\bribs\b"),
    ("poultry", r"chicken|turkey|\bduck\b|cornish hen|poultry"),
    ("meat_other", r"\bmeat\b|venison|\bbison\b|\bveal\b"),
    ("olive_oil", r"olive oil"),
    ("rapeseed_oil", r"canola|rapeseed|sunflower oil|safflower"),
    ("vegetable_oil", r"\boil\b|margarine|shortening|crisco|cooking spray|^spray$"),
    ("oats", r"\boats?\b|oatmeal|granola"),
    ("rice", r"quinoa|\brice\b(?! vinegar| wine)|risotto|arborio|basmati|jasmine"),
    ("maize", r"corn ?starch|cornmeal|corn meal|polenta|grits|masa|corn tortilla|tortilla chips|corn chips|popcorn|\bcorn\b|hominy"),
    ("wheat", r"manicotti|pie shell|crouton|flour|bread|crumbs|panko|pasta|spaghetti|macaroni|noodle|linguine|penne|fettuccine|lasagna|ravioli|tortellini|tortilla|cracker|biscuit|pie crust|pastry|phyllo|croutons|couscous|bulgu?r|barley|\brye\b|wheat|pretzel|bagel|bun\b|buns|roll\b|rolls|pita|baguette|semolina|orzo|gnocchi|wonton|dough"),
    ("potatoes", r"potato(?!es? chips)|potatoes|hash browns?|tater"),
    ("root_veg", r"sweet potato|yam|carrot|beet|parsnip|turnip|radish|rutabaga|celeriac|ginger root|fresh ginger|gingerroot"),
    ("tomatoes", r"tomato|salsa|marinara|spaghetti sauce|pasta sauce|pizza sauce|enchilada sauce"),
    ("onions", r"onion|shallot|leek|scallion|garlic(?! powder| salt)|chive"),
    ("brassicas", r"cabbage|broccoli|cauliflower|kale|brussels|bok choy|collard|coleslaw"),
    ("peas", r"\bpeas?\b|snow peas|sugar snap"),
    ("pulses", r"beans?\b(?! sprout)|lentil|chickpea|garbanzo|hummus|black-eyed|refried"),
    ("citrus", r"lemon|lime|orange|grapefruit|tangerine|clementine|citrus|zest"),
    ("apples", r"apple|cider"),
    ("bananas", r"banana"),
    ("berries", r"berr|grape(?!fruit)|raisin|currant|cranberr|cherr"),
    ("fruit", r"guacamole|pineapple|peach|pear\b|pears|mango|apricot|plum|prune|date|fig|kiwi|melon|papaya|coconut|avocado|rhubarb|pomegranate|fruit|pumpkin(?! seed)"),
    ("nuts", r"almond|walnut|pecan|cashew|pistachio|hazelnut|macadamia|pine nut|\bnuts?\b|sesame seed|sunflower seed|pumpkin seed|chia|flax|seeds?$|tahini"),
    ("sugar", r"oreo|butterscotch|sugar|syrup|honey|molasses|\bjam\b|jelly|preserves|marmalade|marshmallow|candy|caramel|splenda|sweetener|agave|frosting|sprinkles"),
    ("spices", r"^ginger$|ground ginger|xanthan|salt|pepper(corn)?s?$|black pepper|white pepper|cayenne|paprika|cumin|cinnamon|nutmeg|clove|allspice|cardamom|turmeric|curry|chili powder|chilli powder|oregano|basil|thyme|rosemary|sage|marjoram|tarragon|dill|bay lea|seasoning|spice|powder|extract|vanilla|baking soda|yeast|cream of tartar|food coloring|flakes|\bmace\b|saffron|fennel seed|caraway|celery seed|mustard seed|coriander|herbs|msg|pectin"),
    ("vegetables", r"caper|sauerkraut|pimiento|pepper|jalapeno|chil(e|i)|poblano|serrano|celery|spinach|lettuce|zucchini|squash|cucumber|mushroom|eggplant|asparagus|artichoke|olive|green beans|okra|sprouts|arugula|watercress|parsley|cilantro|mint|chard|fennel|bamboo|water chestnut|vegetable|greens|herb"),
]
_COMPILED = [(k, re.compile(p)) for k, p in RULES]


# Entries of ingredients_title that are not ingredients (template text, notes, kitchen items).
NOT_FOOD = re.compile(r"\$|template|^\(?optional\)?$|^(mix|each|halves|pieces|total|find|round|savory)$|"
                      r"^use (more|less)|be substituted|to directions|foil|plastic wrap|toothpick|skewer|parchment")


def is_food(name):
    return bool(name) and not NOT_FOOD.search(name.lower())


def classify_rule(name):
    """Return a group key for an ingredient name, or UNCLASSIFIED."""
    n = name.lower().strip()
    if not n or "$" in n:
        return UNCLASSIFIED
    for key, rx in _COMPILED:
        if rx.search(n):
            return key
    return UNCLASSIFIED


# Allergens a group does not carry but a specific ingredient name reveals.
EXTRA_ALLERGENS = [
    (re.compile(r"worcestershire|caesar|fish sauce|anchov"), "fish"),
    (re.compile(r"sesame|tahini"), "sesame"),
    (re.compile(r"mustard"), "mustard"),
    (re.compile(r"celery"), "celery"),
    (re.compile(r"soy sauce|teriyaki|hoisin"), "gluten"),
    (re.compile(r"mayonnaise|\bmayo\b|egg noodle|meringue"), "eggs"),
    (re.compile(r"\bwine\b|sherry|dried apricot"), "sulphites"),
    (re.compile(r"lupin"), "lupin"),
    (re.compile(r"cream of \w+ soup|pesto|ranch"), "milk"),
]
