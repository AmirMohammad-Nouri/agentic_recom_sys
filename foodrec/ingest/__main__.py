"""CLI: python -m foodrec.ingest [--no-llm] [--fixture N] [--xlsx PATH] [--out PATH] [--cache PATH]"""
import argparse
import logging

from ..config import ROOT, load_settings
from .build import build


def main():
    ap = argparse.ArgumentParser(description="Build the FoodRec catalogue from the HUMMUS spreadsheet.")
    ap.add_argument("--xlsx", default=str(ROOT / "data" / "hummus" / "hummus_recipes.xlsx"))
    ap.add_argument("--out", default=None, help="default: data/hummus/catalogue.json.gz")
    ap.add_argument("--cache", default=str(ROOT / "data" / "hummus" / "labels.json"),
                    help="label cache; copy it to another device to skip the model there")
    ap.add_argument("--no-llm", action="store_true", help="keyword rules only")
    ap.add_argument("--model", default=None, help="Ollama model (default: FOODREC_LLM_MODEL)")
    ap.add_argument("--fixture", type=int, default=None, help="build a random sample of N rows (tests)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    llm = None
    if not a.no_llm:
        from ..llm import OllamaLLM
        s = load_settings()
        if a.model:
            s.llm_model = a.model
        llm = OllamaLLM(s)
        st = llm.status(refresh=True)
        if not st.get("available"):
            print(f"Ollama model not available ({st.get('reason')}); continuing with keyword rules.")
            llm = None
    out = a.out or str(ROOT / "data" / ("fixture/catalogue.json.gz" if a.fixture else "hummus/catalogue.json.gz"))
    build(a.xlsx, out, a.cache, llm=llm, limit=a.fixture)


if __name__ == "__main__":
    main()
