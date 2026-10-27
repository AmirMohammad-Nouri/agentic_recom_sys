"""Deterministic parsing of one HUMMUS row: amounts, grams, nutrients, tags.

Nothing here needs a language model. The model is only used (classify.py) to map the
free-text ingredient names to food groups and to label cuisine and meal type; this module
supplies the rule-based fallback for those too.
"""
import ast
import re

NUTRIENT_COLUMNS = {   # catalogue key: (per 100 g column, per serving column)
    "kcal": ("calories_per_100g [cal]", "calories_per_serving [cal]"),
    "fat_g": ("totalfat_per_100g [g]", "totalfat_per_serving [g]"),
    "sat_fat_g": ("saturatedfat_per_100g [g]", "saturatedfat_per_serving [g]"),
    "cholesterol_mg": ("cholesterol_per_100g [mg]", "cholesterol_per_serving [mg]"),
    "sodium_mg": ("sodium_per_100g [mg]", "sodium_per_serving [mg]"),
    "fibre_g": ("dietaryfiber_per_100g [g]", "dietaryfiber_per_serving [g]"),
    "sugars_g": ("sugars_per_100g [g]", "sugars_per_serving [g]"),
    "protein_g": ("protein_per_100g [g]", "protein_per_serving [g]"),
    "carbs_g": ("totalcarbohydrate_per_100g [g]", "totalcarbohydrate_per_serving [g]"),
}
# Rows whose per-100 g values are physically impossible are dropped and counted.
IMPOSSIBLE = {"kcal": 900, "fat_g": 100, "sat_fat_g": 100, "sugars_g": 100, "protein_g": 100,
              "fibre_g": 100, "carbs_g": 100}

ML = {"cup": 240, "tablespoon": 15, "tbsp": 15, "teaspoon": 5, "tsp": 5, "pint": 473, "quart": 946,
      "gallon": 3785, "fluid ounce": 29.6, "fl oz": 29.6, "ml": 1, "milliliter": 1, "l": 1000, "liter": 1000,
      "litre": 1000, "dl": 100, "cl": 10}
GRAMS = {"ounce": 28.35, "oz": 28.35, "lb": 453.6, "pound": 453.6, "g": 1, "gram": 1, "kg": 1000,
         "pinch": 0.4, "dash": 0.6, "can": 400, "package": 225, "pkg": 225, "stick": 113, "slice": 25,
         "clove": 5, "stalk": 40, "rib": 40, "head": 500, "bunch": 100, "sprig": 1, "leaf": 0.5,
         "envelope": 30, "packet": 30, "jar": 400, "bottle": 350, "bag": 340, "box": 340,
         "container": 450, "drop": 0.05, "loaf": 450, "fillet": 150, "piece": 50}
ITEM_G = [  # grams of one counted item, by name (checked before the food-group default)
    (r"\begg", 50), (r"yolk", 18), (r"white", 33), (r"garlic|clove", 5), (r"shallot", 30),
    (r"green onion|scallion", 15), (r"onion", 150), (r"sweet potato", 180), (r"potato", 200),
    (r"tomato", 120), (r"carrot", 60), (r"celery", 40), (r"jalapeno|chil", 15), (r"pepper", 150),
    (r"lemon", 100), (r"lime", 65), (r"orange", 150), (r"apple", 180), (r"banana", 120),
    (r"avocado", 170), (r"breast", 170), (r"thigh", 110), (r"chop", 150), (r"tortilla", 40),
    (r"bread|bun|roll", 30), (r"bacon", 25), (r"zucchini", 200), (r"cucumber", 300),
    (r"mushroom", 18), (r"bay lea", 0.2),
]
_ITEM_G = [(re.compile(p), g) for p, g in ITEM_G]
_NUM = re.compile(r"^\s*([\d./]+(?:\s+\d+/\d+)?)(?:\s*-\s*([\d./]+(?:\s+\d+/\d+)?))?")
_PKG = re.compile(r"\(\s*([\d./]+(?:\s+\d+/\d+)?)\s*(ounce|oz|lb|g|kg|ml|fluid ounce)s?\s*\)")


def _num(s):
    s = s.strip()
    total = 0.0
    for part in s.split():
        if "/" in part:
            a, b = part.split("/", 1)
            total += float(a) / float(b) if float(b) else 0.0
        else:
            total += float(part)
    return total


def parse_amount(amount, name, density, unit_g):
    """'1 1/2 time(s) cups ' -> (grams, basis text). Ranges use their midpoint."""
    a = (amount or "").replace("time(s)", " ").strip().lower()
    qty = 1.0
    m = _NUM.match(a)
    if m and m.group(1):
        try:
            lo = _num(m.group(1))
            hi = _num(m.group(2)) if m.group(2) else lo
            qty = (lo + hi) / 2 or 1.0
        except (ValueError, ZeroDivisionError):
            qty = 1.0
        a = a[m.end():].strip()
    pkg = _PKG.search(a)
    if pkg:
        size = _num(pkg.group(1))
        unit = pkg.group(2)
        g = size * (ML[unit] * density if unit in ML else GRAMS.get(unit, 1))
        return qty * g, f"{qty:g} x {size:g} {unit} pack"
    unit = re.sub(r"[^a-z ]", " ", a).strip()
    for u in sorted(list(ML) + list(GRAMS), key=len, reverse=True):
        if re.match(rf"{u}(e?s)?\b", unit):
            if u in ML:
                return qty * ML[u] * density, f"{qty:g} {u} x {ML[u]:g} ml x {density:g} g/ml"
            return qty * GRAMS[u], f"{qty:g} {u} x {GRAMS[u]:g} g"
    n = name.lower()
    for rx, g in _ITEM_G:
        if rx.search(n):
            return qty * g, f"{qty:g} item x {g:g} g"
    return qty * unit_g, f"{qty:g} item x {unit_g:g} g (food-group default)"


def fix_text(s):
    """Repair UTF-8 text that was decoded as Latin-1 in the source ("SautÃ©ed" -> "Sautéed")."""
    s = str(s)
    if "Ã" in s or "â€" in s:
        try:
            return s.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            try:
                return s.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                return s
    return s


def clean_name(name):
    """Normalise an ingredients_title entry: drop brackets, packaging words, prep adjectives."""
    n = fix_text(name).lower()
    n = re.sub(r"\(.*?\)|\(.*$", " ", n)
    n = re.sub(r"\b(packages?|pkgs?|slices?|cloves?(?= garlic)|stalks?|ribs?(?= celery)|pinch|dash|quarts?|cans?|jars?|"
               r"finely|freshly|firmly|lightly|packed|large|medium|small|fresh|frozen|chopped|minced|diced|sliced|"
               r"shredded|grated|melted|softened|beaten|cooked|uncooked|boneless|skinless|optional|to taste)\b", " ", n)
    n = re.sub(r"[^a-z&' -]", " ", n)
    n = re.sub(r"\s+", " ", n).strip(" -&'")
    if n in ("garlic cloves", "clove garlic", "garlic clove"):
        n = "garlic"
    return n or name.lower().strip()


def literal(s, default):
    try:
        return ast.literal_eval(s) if isinstance(s, str) else default
    except (ValueError, SyntaxError):
        return default


def ingredient_lines(row):
    """[(display text, amount)] from the 'ingredients' column, in recipe order."""
    d = literal(row["ingredients"], {})
    return [(str(t), str(a)) for sec in (d.values() if isinstance(d, dict) else []) for t, a in sec]


def nutrition(row):
    per100, serv = {}, {}
    for k, (c100, cs) in NUTRIENT_COLUMNS.items():
        per100[k] = _f(row.get(c100))
        serv[k] = _f(row.get(cs))
    bad = [k for k, lim in IMPOSSIBLE.items() if per100.get(k) is not None and per100[k] > lim]
    grams = None
    if per100.get("kcal") and serv.get("kcal"):
        grams = round(serv["kcal"] / per100["kcal"] * 100, 1)
    return per100, serv, grams, bad


def _f(v):
    try:
        x = float(v)
        return None if x != x else round(x, 3)
    except (TypeError, ValueError):
        return None


# ---- rule-based cuisine and meal labels (fallback when no model is available) -----------
CUISINES = ["Italian", "Mexican", "Chinese", "Japanese", "Thai", "Indian", "Mediterranean", "Middle Eastern",
            "French", "American", "Other"]
MEALS = ["breakfast", "main", "side", "salad", "soup", "dessert", "snack", "drink", "sauce", "non_food"]
# food.com also hosts household and pet "recipes"; these are dropped from the catalogue
NON_FOOD = re.compile(r"\b(cleaner|cleaning|declogger|detergent|laundry|fabric softener|soap|shampoo|lotion|"
                      r"(salt|sugar|sink) scrub|play ?dough|potpourri|weed killer|air freshener|bubble bath|"
                      r"facial|pore mask|hair (treatment|clarifier)|dog (treats?|biscuits?|cookies|food|shampoo)|"
                      r"doggie|for dogs|jewelry|bird ?seed|suet)\b", re.I)
_CUISINE_RULES = [
    ("Italian", r"pasta|lasagn|spaghetti|risotto|pesto|marinara|alfredo|bruschetta|pizza|tiramisu|gnocchi|italian|carbonara|ravioli|focaccia|penne|fettuccin|linguin|minestrone|cacciatore|parmigiana|ziti|tortellini|calzone|biscotti"),
    ("Mexican", r"taco|burrito|enchilada|quesadilla|salsa|fajita|nacho|guacamole|mexican|tamale|chipotle|pico de|tortilla soup|chimichanga|tex-mex|churro|mole\b"),
    ("Chinese", r"chinese|wonton|chow mein|lo mein|fried rice|kung pao|sweet and sour|szechuan|sichuan|hoisin|egg roll|dumpling|general tso|orange chicken|stir.?fry"),
    ("Japanese", r"japanese|sushi|teriyaki|miso|tempura|ramen|udon|yakitori|katsu"),
    ("Thai", r"\bthai\b|pad thai|satay|lemongrass|green curry|red curry"),
    ("Indian", r"indian|masala|tikka|\bdal\b|dhal|tandoori|naan|biryani|korma|vindaloo|chutney|paneer|samosa|curry"),
    ("Mediterranean", r"greek|tzatziki|gyro|souvlaki|moussaka|mediterranean|spanakopita|paella|spanish|tapas|gazpacho"),
    ("Middle Eastern", r"hummus|falafel|tahini|shawarma|kebab|kabob|tabbouleh|pita|moroccan|tagine|lebanese|persian|turkish|baba ganoush|couscous"),
    ("French", r"french|quiche|crepe|souffle|gratin|bourguignon|ratatouille|provencal|croissant|bechamel|vichyssoise|nicoise|coq au"),
    ("American", r"bbq|barbecue|burger|meatloaf|casserole|cornbread|cookie|brownie|muffin|pancake|mac and cheese|macaroni and cheese|buffalo|sloppy|cobbler|pie\b|biscuits|cajun|creole|southern|chili\b|sandwich|hot dog|coleslaw|potato salad|pot roast"),
]
_MEAL_RULES = [
    ("drink", r"smoothie|punch|lemonade|cocktail|margarita|milkshake|shake\b|latte|cocoa|\btea\b|eggnog|sangria|martini|cider\b"),
    ("dessert", r"cake|cookie|brownie|\bpie\b|pudding|frosting|icing|fudge|candy|cheesecake|ice cream|cupcake|\btart\b|cobbler|crisp\b|crumble|truffle|dessert|sweet roll|cinnamon roll|bars?\b|toffee|meringue|mousse|sorbet|scone|danish|doughnut|donut|brittle|macaroon"),
    ("breakfast", r"pancake|waffle|omelet|omelette|granola|oatmeal|porridge|french toast|scrambled|breakfast|frittata|hash brown|quiche|eggs benedict"),
    ("sauce", r"sauce|dressing|marinade|gravy|\bdip\b|salsa|seasoning|rub\b|spread|butter\b|vinaigrette|glaze|syrup|relish|chutney|jam\b|jelly|pesto|mayonnaise"),
    ("soup", r"soup|chowder|stew|bisque|gumbo|chili\b|broth"),
    ("salad", r"salad|slaw"),
    ("snack", r"muffin|bread\b|biscuit|cracker|appetizer|snack|bites|nachos|popcorn|trail mix|deviled|wings|chips"),
    ("side", r"potatoes|rice\b|beans\b|vegetables|asparagus|broccoli|carrots|green beans|corn\b|stuffing|side|roasted \w+$|mashed"),
]
_CR = [(k, re.compile(p)) for k, p in _CUISINE_RULES]
_MR = [(k, re.compile(p)) for k, p in _MEAL_RULES]


def cuisine_rule(title, ingredients):
    t = title.lower()
    for k, rx in _CR:
        if rx.search(t):
            return k
    ings = " ".join(ingredients).lower()
    for k, rx in _CR[:6]:
        if rx.search(ings):
            return k
    return "Other"


def meal_rule(title):
    t = title.lower()
    if NON_FOOD.search(t):
        return "non_food"
    for k, rx in _MR:
        if rx.search(t):
            return k
    return "main"
