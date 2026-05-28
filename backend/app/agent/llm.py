"""
LLMClient — 多模型工厂

设计动机：
  ShadowOS 需要支持多种 LLM 提供商（OpenAI / DeepSeek / Claude），
  不同模型的 API 格式、定价、Token 计算各有差异。
  LLMClient 提供统一接口，隐藏具体差异。

支持的后端：
  - OpenAI (GPT-4o, GPT-4o-mini)
  - DeepSeek (deepseek-chat, deepseek-reasoner) — 兼容 OpenAI API 格式
  - Claude (claude-opus-4-6, claude-sonnet-4-6) — 需要 Anthropic SDK

模型选择逻辑：
  优先级：config.LLM_PROVIDER > 环境变量 > 默认值 deepseek
  不同节点可指定不同模型（如 Plan 用 opus，Reflect 用 sonnet 以节省成本）

使用方式：
  client = LLMClient()
  response = await client.chat(messages, tools=selector.get_llm_tools())
  plan_json = await client.plan(task_description, state, selector)
"""
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)


# ====================================================================
# LLMResponse — 统一响应模型
# ====================================================================


@dataclass
class LLMResponse:
    """统一的 LLM 响应结构，屏蔽不同提供商的差异"""
    content: str = ""
    tool_calls: list[dict[str, Any]] = None  # type: ignore[assignment]
    model: str = ""
    tokens_used: int = 0
    cost: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.tool_calls is None:
            self.tool_calls = []

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def first_tool_call(self) -> Optional[dict[str, Any]]:
        if self.tool_calls:
            return self.tool_calls[0]
        return None


# ====================================================================
# 模型定价表（USD / 1M tokens）
# ====================================================================

_MODEL_PRICING: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    # DeepSeek
    "deepseek-chat": {"input": 0.27, "output": 1.10},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    "deepseek-v4-flash": {"input": 0.27, "output": 1.10},  # 待确认实际定价
    # Claude (via Anthropic)
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
}


class LLMClient:
    """
    多模型 LLM 客户端

    核心设计：
      — 通过 langchain 的 ChatOpenAI 兼容 OpenAI 和 DeepSeek
      — Claude 通过 langchain-anthropic 的 ChatAnthropic
      — 统一返回 LLMResponse dataclass
      — 自动计算 Token 消耗和费用
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> None:
        self.provider = provider or settings.LLM_PROVIDER
        self.model_name = model_name or settings.LLM_MODEL_NAME
        self.temperature = (
            temperature if temperature is not None else settings.LLM_TEMPERATURE
        )
        self.api_key = settings.LLM_API_KEY
        self.api_base = settings.LLM_API_BASE

        self._chat_model = None

    # ================================================================
    # 主接口
    # ================================================================

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: str = "auto",
        model: Optional[str] = None,
    ) -> LLMResponse:
        """
        发送消息到 LLM，返回统一响应

        Args:
            messages:    OpenAI 格式的消息列表 [{"role": "...", "content": "..."}]
            tools:       Function calling 工具列表
            tool_choice: "auto" / "none" / "required" / 指定 tool name
            model:       覆盖默认模型名

        Returns:
            LLMResponse: content + tool_calls + tokens + cost
        """
        chat_model = self._get_chat_model(model)
        kwargs: dict[str, Any] = {"messages": messages}

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        try:
            response = await chat_model.ainvoke(**kwargs)
            return self._parse_response(response)
        except Exception as exc:
            logger.error("LLM 调用失败: provider=%s, model=%s, error=%s",
                         self.provider, model or self.model_name, exc)
            return LLMResponse(
                content=f"LLM 调用失败: {exc}",
                model=model or self.model_name,
            )

    async def plan(
        self,
        task_description: str,
        tools: list[dict[str, Any]],
        system_prompt: str = "",
        model: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        调用 LLM 规划任务步骤

        Returns:
            解析后的 JSON 步骤列表
        """
        messages = [
            {"role": "system", "content": system_prompt or "You are a task planner."},
            {"role": "user", "content": task_description},
        ]
        response = await self.chat(
            messages=messages,
            tools=tools,
            tool_choice="none",  # Plan 阶段不需要 function call
            model=model,
        )
        return self._extract_json_array(response.content)

    async def reflect(
        self,
        prompt: str,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        调用 LLM 评估执行结果

        Returns:
            {"decision": "continue|retry|replan|complete|abort", "reason": "...", ...}
        """
        messages = [{"role": "user", "content": prompt}]
        response = await self.chat(
            messages=messages,
            tools=None,
            tool_choice="none",
            model=model,
        )
        return self._extract_json_object(response.content)

    # ================================================================
    # 模型管理
    # ================================================================

    def _get_chat_model(self, model: Optional[str] = None):
        """获取或创建 langchain ChatModel 实例"""
        actual_model = model or self.model_name

        # 缓存优化：同模型同参数复用
        if self._chat_model is not None:
            return self._chat_model

        if self.provider == "claude":
            return self._create_claude_model(actual_model)
        else:
            return self._create_openai_compatible_model(actual_model)

    def _create_openai_compatible_model(self, model: str):
        """创建 OpenAI 兼容模型（OpenAI / DeepSeek）"""
        from langchain_openai import ChatOpenAI

        self._chat_model = ChatOpenAI(
            model=model,
            api_key=self.api_key,
            base_url=self.api_base,
            temperature=self.temperature,
            max_tokens=settings.LLM_MAX_TOKENS,
        )
        return self._chat_model

    def _create_claude_model(self, model: str):
        """创建 Claude 模型"""
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError(
                "langchain-anthropic 未安装。请运行: pip install langchain-anthropic"
            )

        self._chat_model = ChatAnthropic(
            model=model,
            api_key=self.api_key,
            temperature=self.temperature,
            max_tokens=settings.LLM_MAX_TOKENS,
        )
        return self._chat_model

    # ================================================================
    # 响应解析
    # ================================================================

    def _parse_response(self, response) -> LLMResponse:
        """解析 langchain 响应为 LLMResponse"""
        from langchain_core.messages import AIMessage

        content = ""
        tool_calls: list[dict[str, Any]] = []
        model = ""
        tokens_used = 0

        if isinstance(response, AIMessage):
            content = response.content if isinstance(response.content, str) else str(response.content)
            model = response.response_metadata.get("model_name", "") if hasattr(response, "response_metadata") else ""

            # 解析 tool_calls
            if hasattr(response, "tool_calls") and response.tool_calls:
                for tc in response.tool_calls:
                    tool_calls.append({
                        "id": tc.get("id", ""),
                        "name": tc.get("name", ""),
                        "arguments": (
                            json.loads(tc["args"]) if isinstance(tc.get("args"), str)
                            else tc.get("args", {})
                        ),
                    })

            # 提取 token 用量
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                tokens_used = (
                    response.usage_metadata.get("input_tokens", 0)
                    + response.usage_metadata.get("output_tokens", 0)
                )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            model=model or self.model_name,
            tokens_used=tokens_used,
            cost=self._calculate_cost(model or self.model_name, tokens_used, 0),
        )

    def _calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        """计算 LLM 调用费用"""
        pricing = _MODEL_PRICING.get(model)
        if not pricing:
            # 尝试模糊匹配
            for name, p in _MODEL_PRICING.items():
                if name in model or model in name:
                    pricing = p
                    break
        if not pricing:
            return Decimal("0")

        cost = (input_tokens / 1_000_000) * pricing["input"] + \
               (output_tokens / 1_000_000) * pricing["output"]
        return Decimal(str(round(cost, 8)))

    # ================================================================
    # JSON 提取
    # ================================================================

    @staticmethod
    def _extract_json_array(text: str) -> list[dict[str, Any]]:
        """从 LLM 输出中提取 JSON 数组"""
        text = text.strip()
        # 去除可能的 markdown 代码块标记
        if text.startswith("```"):
            text = text[text.index("\n") + 1:] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
            if isinstance(result, dict) and "steps" in result:
                return result["steps"]
        except json.JSONDecodeError:
            # 尝试提取 [...] 部分
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass

        logger.warning("无法解析 LLM 返回的 JSON 数组: %s...", text[:200])
        return []

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any]:
        """从 LLM 输出中提取 JSON 对象"""
        text = text.strip()
        if text.startswith("```"):
            text = text[text.index("\n") + 1:] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass

        logger.warning("无法解析 LLM 返回的 JSON: %s...", text[:200])
        return {"decision": "abort", "reason": f"无法解析 LLM 输出: {text[:200]}"}
