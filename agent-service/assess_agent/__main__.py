import uvicorn
from .settings import Settings

if __name__ == "__main__":
    settings = Settings()
    uvicorn.run("assess_agent.app:app", host=settings.host, port=settings.port, log_level="info")
