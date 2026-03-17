import base64
import logging
from abc import ABC, abstractmethod
from openai import AsyncOpenAI
from utils.text_parser import extract_json_from_text
from config import LLM_PROVIDER, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME

logger = logging.getLogger(__name__)

class BaseOCRProvider(ABC):
    @abstractmethod
    async def analyze_receipt(self, image_path: str) -> dict:
        """分析小票，返回包含 amount, category, description 的字典"""
        pass

    def _encode_image(self, image_path: str) -> str:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def _get_prompt(self) -> str:
        return """
        你是一个财务记账助手。请分析这张收据/小票图片，提取出消费信息，并严格以JSON格式返回。
        必须包含以下三个字段：
        - "amount": 消费总金额（数字，浮点数，不要带货币符号）
        - "category": 消费类别（如：餐饮、交通、购物、日用、娱乐等，限4个字以内）
        - "description": 消费详情简述（如：7-11便利店 买水和饭团）
        只返回JSON，不要返回任何解释或其他文字。如果无法识别金额，amount 返回 0。
        """

# === 1. OpenAI 兼容接口 (支持 OpenAI, 阿里 Qwen-VL, DeepSeek-VL, 零一万物, 甚至 Ollama) ===
class OpenAICompatibleProvider(BaseOCRProvider):
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL
        )
        self.model = LLM_MODEL_NAME

    async def analyze_receipt(self, image_path: str) -> dict:
        base64_image = self._encode_image(image_path)
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content":[
                            {"type": "text", "text": self._get_prompt()},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ]
                    }
                ],
                temperature=0.1 # 低温度保证输出稳定性
            )
            raw_text = response.choices[0].message.content
            return extract_json_from_text(raw_text)
        except Exception as e:
            logger.error(f"OpenAI Compatible OCR failed: {e}")
            return {}

# === 2. Google Gemini 接口 ===
class GeminiProvider(BaseOCRProvider):
    def __init__(self):
        import google.generativeai as genai
        genai.configure(api_key=LLM_API_KEY)
        self.model = genai.GenerativeModel(LLM_MODEL_NAME or 'gemini-1.5-flash')

    async def analyze_receipt(self, image_path: str) -> dict:
        import PIL.Image
        try:
            img = PIL.Image.open(image_path)
            # Gemini Python SDK 支持直接传 PIL Image
            response = await self.model.generate_content_async([self._get_prompt(), img])
            return extract_json_from_text(response.text)
        except Exception as e:
            logger.error(f"Gemini OCR failed: {e}")
            return {}

# === 3. Provider 工厂模式 ===
def get_ocr_provider() -> BaseOCRProvider:
    if LLM_PROVIDER == "gemini":
        return GeminiProvider()
    elif LLM_PROVIDER == "claude":
        # 类似地，你可以实现 ClaudeProvider
        pass 
    
    # 默认使用 OpenAI 兼容模式
    return OpenAICompatibleProvider()

# 全局单例
ocr_provider = get_ocr_provider()
