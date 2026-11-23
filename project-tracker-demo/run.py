from pathlib import Path

import uvicorn
from dotenv import load_dotenv

if __name__ == "__main__":
    load_dotenv(Path(__file__).with_name(".env"))
    from tracker.web import make_app

    uvicorn.run(make_app(), host="127.0.0.1", port=8711)
