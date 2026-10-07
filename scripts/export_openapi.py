"""Export the integration schema without loading real credentials or services."""

import json
from pathlib import Path

from app.config import Settings
from app.main import create_app

if __name__ == "__main__":
    app = create_app(
        Settings(
            _env_file=None,
            environment="development",
            planner_mode="demo",
            backend_mode="mock",
            redis_url="",
            ai_service_token="schema-export-placeholder-not-a-real-secret",
        )
    )
    Path("docs/openapi.json").write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("Exported docs/openapi.json")
