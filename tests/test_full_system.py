"""Comprehensive end-to-end unit tests for foodrec."""
import os
import unittest
from foodrec.catalogue import Catalogue
from foodrec.features import ReferenceScaler
from foodrec.db import Database
from foodrec.graph import KnowledgeGraph
from foodrec.rag import FoodRAG
from foodrec.learner import Learner
from foodrec.engine import recommend
from foodrec.supervisor import SupervisorAgent


class TestFullSystem(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_foodrec.db"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.db = Database(self.db_path)
        self.cat = Catalogue.load("data/fixture/recipes.json")
        self.scaler = ReferenceScaler.fit(self.cat)
        self.kg = KnowledgeGraph()
        self.kg.seed_from_catalogue(self.cat)
        self.rag = FoodRAG()
        self.rag.index_catalogue_and_ingredients(self.cat, {})
        self.supervisor = SupervisorAgent(self.kg, self.rag)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_database_versioning(self):
        uid = "user_1"
        v1 = self.db.save_profile(uid, {"allergens": ["peanuts"]})
        v2 = self.db.save_profile(uid, {"allergens": ["peanuts", "milk"]})
        self.assertEqual(v1, 1)
        self.assertEqual(v2, 2)
        latest = self.db.get_latest_profile(uid)
        self.assertEqual(latest["version"], 2)
        self.assertIn("milk", latest["declared"]["allergens"])

    def test_supervisor_conflict_analysis(self):
        uid = "user_2"
        declared = {"allergens": []}
        goal = {"weights": {"p": 0.2, "h": 0.2, "s": 0.2, "c": 0.2, "t": 0.2}, "diversity": 0.3, "mode": "lightweight"}
        learner = Learner(self.cat)
        learner.apply({"event_id": "e1", "type": "profile_created", "ts": 0.0, "payload": {"declared": declared, "weights": goal["weights"]}})

        res = self.supervisor.analyze(uid, declared, goal, learner)
        self.assertIn("reasoning_trace", res)
        self.assertTrue(len(res["reasoning_trace"]) >= 4)

    def test_recommendation_pipeline(self):
        learner = Learner(self.cat)
        declared = {"allergens": ["peanuts"]}
        config = {"weights": {"p": 0.3, "h": 0.25, "s": 0.2, "c": 0.15, "t": 0.1}, "diversity": 0.3, "mode": "lightweight", "k": 5}
        res = recommend(self.cat, self.scaler, learner, declared, config, now=0.0)
        self.assertEqual(len(res["results"]), 5)

    def test_nutrient_aware_chat_agent(self):
        from foodrec.mcp_tools import MCPToolRegistry
        from foodrec.agent import SupervisorChatAgent
        import json
        with open("data/fixture/ingredients.json") as f:
            ing_db = json.load(f)

        learners = {"user_nutri": Learner(self.cat)}
        mcp = MCPToolRegistry(self.db, self.cat, self.scaler, self.kg, self.rag, learners, ing_db=ing_db)
        agent = SupervisorChatAgent(mcp, self.supervisor, model="qwen3:1.7b")

        prompt = "if i want to have more protein and sugar in my next meal, what you recommend to me>?"
        res = agent.process_chat_prompt("user_nutri", prompt)

        self.assertIn("response", res)
        self.assertIn("reasoning_trace", res)
        # Verify 5 distinct reasoning steps
        self.assertGreaterEqual(len(res["reasoning_trace"]), 5)
        # Verify protein and sugar perception
        self.assertIn("Protein", res["response"])
        self.assertIn("Sugar", res["response"])
        # Verify ingredient attribution in response
        self.assertIn("Where the Protein comes from", res["response"])
        self.assertIn("Where the Sugar comes from", res["response"])


if __name__ == "__main__":
    unittest.main()