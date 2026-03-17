import json
import logging
import base64
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key="YOUR_OPENAI_API_KEY")

async def analyze_receipt(image_path: str) -> dict:
    """
    通过 AI 识别小票图像，返回结构化的记账数据
    """
    with open(image_path, "rb") as image_file:
        base64_image = base64.b64encode(image_file.read()).decode('utf-8')

    prompt = """
    你是一个财务助手。请分析这张收据/小票，并严格以JSON格式返回以下字段：
    - amount: 消费总金额（浮点数）
    - category: 消费类别（如：餐饮、交通、购物、日用、娱乐等）
    - description: 消费详情简述（如：7-11便利店 咖啡和饭团）
    不要返回任何其他内容或 Markdown 标记。
    """
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content":[
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }
            ],
            response_format={ "type": "json_object" }
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"Receipt OCR failed: {e}")
        return None
