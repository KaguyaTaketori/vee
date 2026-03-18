import re
import json
import logging

logger = logging.getLogger(__name__)

def extract_json_from_text(text: str) -> dict:
    """从 LLM 返回的文本中安全提取 JSON"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 使用正则提取包裹在 {} 中的内容
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            logger.error(f"正则提取后依然无法解析 JSON: {e}\n原文: {text}")
    
    return {}
