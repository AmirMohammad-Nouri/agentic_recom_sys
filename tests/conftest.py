import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
# Tests use the built-in defaults, never the repository's foodrec.config.json.
os.environ["FOODREC_CONFIG"] = "none"
# ...and the "open" mode (no sign-in; the browser names the user), unless a test sets another one.
os.environ["FOODREC_MODE"] = "open"
os.environ.pop("FOODREC_ADMIN_TOKEN", None)

from fakes import FakeLLM  # noqa: E402

from foodrec.catalogue import Catalogue  # noqa: E402
from foodrec.config import ROOT, load_settings  # noqa: E402
from foodrec.features import Scorer  # noqa: E402
from foodrec.learner import Learner  # noqa: E402
from foodrec.ranker import DEFAULT_WEIGHTS  # noqa: E402


FIXTURE = ROOT / "data" / "fixture" / "catalogue.json.gz"


class OfflineLLM(FakeLLM):
    def status(self, refresh=False):
        return {"available": False, "model": "none", "embed_model_ready": False, "reason": "offline (test)"}


@pytest.fixture(scope="session")
def cat():
    return Catalogue.load(FIXTURE)


@pytest.fixture(scope="session")
def scaler(cat):
    return Scorer(cat)


@pytest.fixture
def learner(cat):
    lr = Learner(cat)
    lr.apply({"event_id": "e0", "type": "profile_created", "ts": 0.0,
              "payload": {"declared": {}, "weights": DEFAULT_WEIGHTS}})
    return lr


def make_app(tmp_path, llm=None):
    from foodrec.service import App
    return App(load_settings(data_dir=tmp_path, qdrant="memory", catalogue_path=FIXTURE), llm=llm or OfflineLLM())


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


@pytest.fixture
def onboarded(app):
    first = app.catalogue.recipes[0]["id"]
    app.onboard("u", {"allergens": ["peanuts"], "preferred_cuisines": ["Italian"], "liked_recipes": [first],
                      "health_goal": "eat_healthier"}, {"h": .3, "s": .5, "t": .2})
    return app
