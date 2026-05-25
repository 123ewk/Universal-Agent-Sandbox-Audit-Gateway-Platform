"""
测试 schemas/common.py 模块
运行方式：pytest tests/test_schemas_common.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import pytest
from pydantic import BaseModel

from app.schemas.common import APIResponse, PaginatedData


# ==================== 测试用辅助模型 ====================
class UserTest(BaseModel):
    """模拟用户模型，用于泛型测试"""
    id: int
    name: str


# ==================== APIResponse 测试 ====================
class TestAPIResponse:
    """验证统一响应的序列化、泛型和工厂方法"""

    def test_basic_success_response(self) -> None:
        resp = APIResponse.success(message="查询成功")
        assert resp.code == "SUCCESS"
        assert resp.message == "查询成功"
        assert resp.data is None

    def test_success_with_data(self) -> None:
        user = UserTest(id=1, name="Alice")
        resp = APIResponse.success(data=user)
        assert resp.data == user
        assert resp.data.id == 1

    def test_success_with_list_data(self) -> None:
        users = [UserTest(id=1, name="Alice"), UserTest(id=2, name="Bob")]
        resp = APIResponse.success(data=users)
        assert isinstance(resp.data, list)
        assert len(resp.data) == 2

    def test_json_serialization(self) -> None:
        """验证 model_dump 输出符合前端期望的 {code, message, data} 格式"""
        resp = APIResponse.success(data=UserTest(id=1, name="Alice"))
        dumped = resp.model_dump()
        assert dumped["code"] == "SUCCESS"
        assert dumped["message"] == "操作成功"
        assert dumped["data"]["id"] == 1
        assert dumped["data"]["name"] == "Alice"

    def test_direct_construction_with_defaults(self) -> None:
        resp = APIResponse()
        assert resp.code == "SUCCESS"
        assert resp.message == "操作成功"
        assert resp.data is None


# ==================== PaginatedData 测试 ====================
class TestPaginatedData:
    """验证分页容器的计算属性"""

    def test_pagination_basic(self) -> None:
        users = [UserTest(id=i, name=f"User{i}") for i in range(1, 6)]
        paged = PaginatedData(items=users, total=100, page=1, page_size=20)
        assert len(paged.items) == 5
        assert paged.total == 100
        assert paged.page == 1
        assert paged.page_size == 20

    def test_total_pages_calculation_exact(self) -> None:
        """总数刚好整除时，总页数计算正确"""
        paged = PaginatedData(items=[], total=100, page=1, page_size=20)
        assert paged.total_pages == 5

    def test_total_pages_calculation_remainder(self) -> None:
        """总数有余数时，总页数向上取整"""
        paged = PaginatedData(items=[], total=101, page=1, page_size=20)
        assert paged.total_pages == 6  # ceil(101/20) = 6

    def test_total_pages_zero_page_size(self) -> None:
        """page_size 为 0 时应返回 0，避免除零错误"""
        paged = PaginatedData(items=[], total=100, page=1, page_size=0)
        assert paged.total_pages == 0

    def test_total_pages_empty(self) -> None:
        """空数据集返回 0 页"""
        paged = PaginatedData(items=[], total=0, page=1, page_size=20)
        assert paged.total_pages == 0

    def test_paginated_data_with_api_response(self) -> None:
        """验证 PaginatedData 嵌套在 APIResponse 中序列化正常"""
        paged = PaginatedData(items=[UserTest(id=1, name="Alice")], total=50, page=1, page_size=20)
        resp = APIResponse.success(data=paged, message="分页查询成功")
        dumped = resp.model_dump()
        assert dumped["data"]["total"] == 50
        assert dumped["data"]["total_pages"] == 3
        assert dumped["data"]["items"][0]["name"] == "Alice"
