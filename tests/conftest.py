import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeLLM  # noqa: E402

from foodrec.catalogue import Catalogue  # noqa: E402
from foodrec.config import ROOT, load_settings  # noqa: E402
from foodrec.features import ReferenceScaler  # noqa: E402
from foodrec.learner import Learner  # noqa: E402
from foodrec.ranker import DEFAULT_WEIGHTS  # noqa: E402


class OfflineLLM(FakeLLM):
    def status(self, refresh=False):
        return {"available": False, "model": "none", "embed_model_ready": False, "reason": "offline (test)"}


@pytest.fixture(scope="session")
def cat():
    return Catalogue.load(ROOT / "data" / "fixture" / "recipes.json")


@pytest.fixture(scope="session")
def scaler(cat):
    return ReferenceScaler.fit(cat)


@pytest.fixture
def learner(cat):
    lr = Learner(cat)
    lr.apply({"event_id": "e0", "type": "profile_created", "ts": 0.0,
              "payload": {"declared": {}, "weights": DEFAULT_WEIGHTS}})
    return lr


def make_app(tmp_path, llm=None):
    from foodrec.service import App
    return App(load_settings(data_dir=tmp_path, qdrant="memory"), llm=llm or OfflineLLM())


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


@pytest.fixture
def onboarded(app):
    app.onboard("u", {"allergens": ["peanuts"], "preferred_cuisines": ["Italian"], "liked_recipes": ["r001"]},
                {"p": .2, "h": .2, "s": .4, "c": .1, "t": .1})
    return app
