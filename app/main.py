from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .admin import admin_page
from .api.routes import router

app = FastAPI(title="task-queue")
app.include_router(router, prefix="/api")
# Browser traffic from the AI Bharatiyaa suite uses a product-scoped API path
# so it cannot collide with the APIs of sibling products. The original /api
# routes remain the stable machine-client contract.
app.include_router(router, prefix="/api/queue", include_in_schema=False)

admin_ui_directory = Path(__file__).with_name("admin_ui")
app.mount(
    "/admin-assets",
    StaticFiles(directory=admin_ui_directory),
    name="admin-assets",
)
app.mount(
    "/queue/admin-assets",
    StaticFiles(directory=admin_ui_directory),
    name="queue-admin-assets",
)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/queue")


@app.get("/admin", include_in_schema=False)
def admin():
    return admin_page()


@app.get("/queue", include_in_schema=False)
@app.get("/queue/", include_in_schema=False)
def queue_admin():
    return admin_page()
