from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from .admin import admin_page
from .api.routes import router

app = FastAPI(title="task-queue")
app.include_router(router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/admin")


@app.get("/admin", include_in_schema=False)
def admin():
    return admin_page()
