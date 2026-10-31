"""CLI: python -m foodrec.ingest [--no-llm] [--fixture N] [--xlsx PATH | --recipes PATH] [--out PATH] [--cache PATH]

Your own dataset:
    python -m foodrec.ingest --recipes my_recipes.json --name my_study --out data/my_study/catalogue.json.gz
  (simple JSON format, see ingest/simple.py), or --xlsx with a .xlsx/.csv in the HUMMUS column layout.
Then set dataset.catalogue_path (and a new dataset.data_dir) in foodrec.config.json.
"""
import argparse
import logging

from ..config import ROOT, load_settings
from .build import build


def main():
    ap = argparse.ArgumentParser(description="Build the FoodRec catalogue from the HUMMUS spreadsheet.")
    ap.add_argument("--xlsx", default=str(ROOT / "data" / "hummus" / "hummus_recipes.xlsx"),
                    help="HUMMUS-layout spreadsheet (.xlsx, .csv or a JSON list of rows)")
    ap.add_argument("--recipes", default=None, help="your own recipes in the simple JSON format (ingest/simple.py)")
    ap.add_argument("--name", default=None, help="catalogue name (default: hummus, or the --recipes file name)")
    ap.add_argument("--id-prefix", default=None, help="prefix of recipe ids (default h; r for --recipes)")
    ap.add_argument("--out", default=None, help="default: data/hummus/catalogue.json.gz")
    ap.add_argument("--cache", default=None,
                    help="label cache; copy it to another device to skip the model there "
                         "(default data/hummus/labels.json, or labels.json next to --out for --recipes)")
    ap.add_argument("--no-llm", action="store_true", help="keyword rules only")
    ap.add_argument("--model", default=None, help="Ollama model (default: FOODREC_LLM_MODEL)")
    ap.add_argument("--fixture", type=int, default=None, help="build a random sample of N rows (tests)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    llm = None
    if not a.no_llm:
        from ..llm import make_llm
        s = load_settings()
        if a.model:
            s.llm_model = a.model
        llm = make_llm(s)
        st = llm.status(refresh=True)
        if not st.get("available"):
            print(f"Ollama model not available ({st.get('reason')}); continuing with keyword rules.")
            llm = None
    if a.recipes:
        from pathlib import Path

        from .simple import rows_from_simple
        name = a.name or Path(a.recipes).stem
        out = a.out or str(ROOT / "data" / name / "catalogue.json.gz")
        cache = a.cache or str(Path(out).parent / "labels.json")
        build(a.recipes, out, cache, llm=llm, rows=rows_from_simple(a.recipes), name=name,
              id_prefix=a.id_prefix or "r", note=f"Own dataset '{name}' ({Path(a.recipes).name}).")
        return
    out = a.out or str(ROOT / "data" / ("fixture/catalogue.json.gz" if a.fixture else "hummus/catalogue.json.gz"))
    build(a.xlsx, out, a.cache or str(ROOT / "data" / "hummus" / "labels.json"), llm=llm, limit=a.fixture,
          name=a.name, id_prefix=a.id_prefix or "h")


if __name__ == "__main__":
    main()
