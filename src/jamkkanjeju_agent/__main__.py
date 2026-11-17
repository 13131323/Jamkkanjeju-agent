"""LLM 없이 get_stored_locations Tool을 단독 실행한다."""

import argparse
import json
import sys

from dotenv import load_dotenv
from langchain_core.tools import ToolException

from .tools import get_stored_locations


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="저장된 장소 조회 Tool 단독 실행")
    parser.parse_args()
    try:
        result = get_stored_locations.invoke({})
    except ToolException as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
