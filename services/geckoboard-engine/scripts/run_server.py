import uvicorn

from geckoboard_engine.config import settings

if __name__ == "__main__":
    uvicorn.run("geckoboard_engine.main:app", host=settings.host, port=settings.port, reload=False)
