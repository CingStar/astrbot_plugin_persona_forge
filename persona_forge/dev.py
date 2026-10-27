"""开发者模式（教学模式）提示词构建。"""

from __future__ import annotations


def build_dev_rewrite_prompt(base: str, knowledge, notes) -> str:
    """Compose the teaching-mode rewrite prompt (kept pure for tests)."""
    knowledge_block = "\n".join(f"- {k}" for k in (knowledge or []))
    notes_block = "\n".join(f"- {n}" for n in (notes or []))
    return (
        "你在维护一个人格提示词。请把下面的【当前人格设定】、【人格专属知识】和"
        "【开发者教学笔记】融合，重写成一份完整、自然、可直接使用的人格系统提示词。\n"
        "要求：\n"
        "1. 保留人格核心身份、语气与边界；\n"
        "2. 认真吸收开发者笔记里的纠正与偏好，但不要逐条复述；\n"
        "3. 不要出现“笔记”“开发者”“记忆文件”等实现细节；\n"
        "4. 不要添加通用助手腔；\n"
        "5. 只输出重写后的提示词正文。\n\n"
        f"【当前人格设定】\n{base}\n\n"
        f"【人格专属知识】\n{knowledge_block or '（无）'}\n\n"
        f"【开发者教学笔记】\n{notes_block or '（无）'}"
    )
