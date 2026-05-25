"""
统一 API 响应结构

设计动机：
  前端期望所有接口返回统一格式 {code, message, data}，
  而不是有的返回裸 list，有的返回 {detail: ...}。
  使用 Pydantic 泛型 Generic[T] 让 data 字段的类型随业务而变化，
  同时保持外层结构不变。

使用示例：
  from app.schemas.common import APIResponse
  @router.get("/users")
  async def list_users() -> APIResponse[list[UserOut]]:
      users = await service.get_users()
      return APIResponse.success(data=users)
"""
from typing import Generic, TypeVar
from pydantic import BaseModel, computed_field

# 泛型参数 T 代表 data 字段的具体类型
# TypeVar 绑定了 BaseModel，约束 data 必须是 Pydantic 模型或基础类型
T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """
    统一成功响应模型

    字段：
      code:   业务状态码，成功固定为 "SUCCESS"
      message: 人类可读的成功信息
      data:   业务数据负载，类型由调用方通过泛型指定
    """
    code: str = "SUCCESS"
    message: str = "操作成功"
    data: T | None = None

    @classmethod
    def success(cls, data: T | None = None, message: str = "操作成功") -> "APIResponse[T]":
        """
        快速创建成功响应的工厂方法
        使用方式: return APIResponse.success(data=users, message="查询成功")
        """
        return cls(code="SUCCESS", message=message, data=data)


class PaginatedData(BaseModel, Generic[T]):
    """
    分页数据容器，与 APIResponse 配合使用
    使用方式: APIResponse.success(data=PaginatedData(items=users, total=100, page=1, page_size=20))
    """
    items: list[T]
    total: int
    page: int
    page_size: int

    @computed_field
    @property
    def total_pages(self) -> int:
        """计算总页数，向上取整"""
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size
