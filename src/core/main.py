# ============================================
# Author: AKO_studio
# Agent: AKO_web_consult_agent
# Generated: 2026-07-30
# ============================================

import argparse
import logging
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


def setup_logging(level="INFO"):
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(str(log_dir / "AKO_web_consult_agent.log"), encoding="utf-8"),
        ],
    )


def main():
    parser = argparse.ArgumentParser(description="Ako Web Consult Agent")
    parser.add_argument("--config", default="config/AKO_web_consult_agent_config.yaml", help="Configuration file path")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    setup_logging(args.log_level)
    logger = logging.getLogger("AKO_web_consult_agent")

    logger.info("Ako Web Consult Agent starting...")
    logger.info(f"Config: {args.config}")
    logger.info(f"Log level: {args.log_level}")
    logger.info("Ako Web Consult Agent running. Placeholder for core logic.")


if __name__ == "__main__":
    main()
