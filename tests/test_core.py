# ============================================
# Author: AKO_studio
# Agent: AKO_web_consult_agent
# Tests for: Ako Web Consult Agent
# ============================================

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAkoWebConsultAgent(unittest.TestCase):

    def setUp(self):
        self.agent_path = Path(__file__).parent.parent

    def test_project_structure(self):
        self.assertTrue((self.agent_path / "src" / "core").exists())
        self.assertTrue((self.agent_path / "config").exists())
        self.assertTrue((self.agent_path / "docs").exists())

    def test_agent_card(self):
        card_path = self.agent_path / "AKO_agent_card.yaml"
        self.assertTrue(card_path.exists())

    def test_requirements(self):
        req_path = self.agent_path / "requirements.txt"
        self.assertTrue(req_path.exists())

    def test_gitignore(self):
        gi_path = self.agent_path / ".gitignore"
        self.assertTrue(gi_path.exists())

    def test_readme(self):
        readme_path = self.agent_path / "README.md"
        self.assertTrue(readme_path.exists())


if __name__ == "__main__":
    unittest.main()
