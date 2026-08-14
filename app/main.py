from fastapi import FastAPI

from .api.routes import router

app = FastAPI(title="task-queue")
app.include_router(router)
