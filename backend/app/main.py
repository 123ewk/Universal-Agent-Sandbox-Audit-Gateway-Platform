"""
ShadowOS — uvicorn 启动入口

启动方式：
  cd backend && uvicorn app.main:app --reload --port 8000
"""
from app.app_factory import create_app

app = create_app()
