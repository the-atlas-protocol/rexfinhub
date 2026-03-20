"""Placeholder holdings router for Render deployment (pending infrastructure upgrade)."""
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")


@router.get("/holdings/{path:path}")
@router.get("/holdings/")
async def holdings_placeholder(request: Request):
    return templates.TemplateResponse("holdings_placeholder.html", {"request": request})
