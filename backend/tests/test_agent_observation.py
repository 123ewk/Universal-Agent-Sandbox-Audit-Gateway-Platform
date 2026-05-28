"""
观察流水线测试

测试范围：
  - ObservationPipeline 主流程
  - Noise Filter（删除 script/style/ad/popup 等）
  - UI Parser（提取 button/input/link/heading）
  - Summarizer（生成摘要）
  - 错误/警告检测
  - 空输入处理
"""
import pytest

from app.agent.observation import ObservationPipeline


class TestObservationPipeline:
    """ObservationPipeline 测试"""

    def test_pipeline_creation(self):
        pipeline = ObservationPipeline(max_summary_length=200)
        assert pipeline.max_summary_length == 200

    def test_noise_filter_removes_script(self):
        pipeline = ObservationPipeline()
        html = "<html><head><script>alert('XSS')</script></head><body><button>Click</button></body></html>"
        cleaned = pipeline._noise_filter(html)
        assert "alert" not in cleaned.lower()

    def test_noise_filter_removes_style(self):
        pipeline = ObservationPipeline()
        html = "<html><style>.btn { color: red; }</style><body><button>Hi</button></body></html>"
        cleaned = pipeline._noise_filter(html)
        assert ".btn" not in cleaned.lower()

    def test_noise_filter_removes_iframe(self):
        pipeline = ObservationPipeline()
        html = '<iframe src="https://ads.com"></iframe><button>Main</button>'
        cleaned = pipeline._noise_filter(html)
        assert "iframe" not in cleaned.lower()

    def test_noise_filter_removes_ads(self):
        pipeline = ObservationPipeline()
        html = '<div class="ad-banner">广告</div><div class="content">内容</div>'
        cleaned = pipeline._noise_filter(html)
        assert "ad-banner" not in cleaned.lower()

    def test_noise_filter_removes_cookie_banner(self):
        pipeline = ObservationPipeline()
        html = '<div class="cookie-banner">接受 Cookie</div><button>登录</button>'
        cleaned = pipeline._noise_filter(html)
        assert "cookie-banner" not in cleaned.lower()

    def test_noise_filter_preserves_button(self):
        pipeline = ObservationPipeline()
        html = '<script>var x=1;</script><button id="login">登录</button>'
        cleaned = pipeline._noise_filter(html)
        assert "login" in cleaned

    def test_noise_filter_preserves_input(self):
        pipeline = ObservationPipeline()
        html = '<style>.a{}</style><input name="search" placeholder="搜索...">'
        cleaned = pipeline._noise_filter(html)
        assert "search" in cleaned

    def test_noise_filter_handles_empty(self):
        pipeline = ObservationPipeline()
        assert pipeline._noise_filter("") == ""

    def test_ui_parser_extracts_buttons(self):
        pipeline = ObservationPipeline()
        html = '<button id="btn1">提交</button><button>取消</button>'
        elements = pipeline._ui_parser(html)
        buttons = [e for e in elements if e["type"] == "button"]
        assert len(buttons) >= 2

    def test_ui_parser_extracts_inputs(self):
        pipeline = ObservationPipeline()
        html = '<input type="text" name="q" placeholder="请输入搜索词">'
        elements = pipeline._ui_parser(html)
        inputs = [e for e in elements if "input" in e["type"]]
        assert len(inputs) >= 1

    def test_ui_parser_extracts_links(self):
        pipeline = ObservationPipeline()
        html = '<a href="/home">首页</a><a href="/about">关于</a>'
        elements = pipeline._ui_parser(html)
        links = [e for e in elements if e["type"] == "link"]
        assert len(links) >= 2

    def test_ui_parser_extracts_headings(self):
        pipeline = ObservationPipeline()
        html = '<h1>欢迎</h1><h2>产品列表</h2>'
        elements = pipeline._ui_parser(html)
        headings = [e for e in elements if e["type"].startswith("h")]
        assert len(headings) >= 2

    def test_summarizer_with_elements(self):
        pipeline = ObservationPipeline()
        elements = [
            {"type": "button", "text": "搜索", "selector": "#btn"},
            {"type": "button", "text": "重置", "selector": "#reset"},
            {"type": "input_text", "text": "关键词", "selector": "#kw"},
        ]
        summary = pipeline._summarizer("<html></html>", "百度搜索", elements)
        assert "百度搜索" in summary
        assert "按钮" in summary or "button" in summary.lower()

    def test_summarizer_empty(self):
        pipeline = ObservationPipeline()
        summary = pipeline._summarizer("", "", [])
        assert "空页面" in summary

    def test_detect_issues_404(self):
        pipeline = ObservationPipeline()
        errors, warnings = pipeline._detect_issues(
            "<html><h1>404 Not Found</h1></html>", "404 Not Found"
        )
        assert any("404" in e for e in errors)

    def test_detect_issues_access_denied(self):
        pipeline = ObservationPipeline()
        errors, warnings = pipeline._detect_issues(
            "<html><h1>Access Denied</h1></html>", "Access Denied"
        )
        assert len(errors) > 0

    def test_detect_issues_captcha(self):
        pipeline = ObservationPipeline()
        errors, warnings = pipeline._detect_issues(
            '<html><div id="captcha">验证码</div></html>', "验证码"
        )
        assert len(errors) > 0

    def test_detect_issues_timeout(self):
        pipeline = ObservationPipeline()
        errors, warnings = pipeline._detect_issues(
            "<html></html>", "Connection timeout"
        )
        assert len(warnings) > 0

    def test_detect_issues_empty_page(self):
        pipeline = ObservationPipeline()
        errors, warnings = pipeline._detect_issues("", "")
        assert any("空白页" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_process_empty_html(self):
        pipeline = ObservationPipeline()
        obs = await pipeline.process("", "https://example.com", "Test")
        assert obs.page_url == "https://example.com"
        assert obs.page_title == "Test"

    @pytest.mark.asyncio
    async def test_process_with_buttons(self):
        pipeline = ObservationPipeline()
        html = """
        <html>
        <head><title>搜索页面</title></head>
        <body>
            <input type="text" name="q" placeholder="搜索">
            <button id="search-btn">搜索</button>
            <a href="/help">帮助</a>
        </body>
        </html>
        """
        obs = await pipeline.process(html, "https://search.com", "搜索页面")
        assert obs.page_title == "搜索页面"
        assert len(obs.interactive_elements) > 0

    @pytest.mark.asyncio
    async def test_process_filters_noise(self):
        pipeline = ObservationPipeline()
        html = """
        <html>
        <head><script>tracking_code()</script><style>.ad{}</style></head>
        <body>
            <div class="advertisement">广告</div>
            <div class="cookie-banner">Cookie提示</div>
            <button>真实按钮</button>
        </body>
        </html>
        """
        obs = await pipeline.process(html)
        assert obs.summary != ""
        # 噪声被过滤
        assert any("button" in str(e).lower() or "按钮" in str(e)
                   for e in obs.interactive_elements)

    @pytest.mark.asyncio
    async def test_process_stores_raw_ref(self):
        pipeline = ObservationPipeline()
        obs = await pipeline.process(
            "<html></html>", raw_data_ref="/tmp/raw_page_001.html"
        )
        assert obs.raw_data_ref == "/tmp/raw_page_001.html"
