"""
文本清洗器
对邮件正文进行去噪：HTML 剥离、回复历史裁剪、签名过滤
"""
import re
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── 回复历史分隔符（多语言） ──
REPLY_SEPARATORS = [
    r"-{2,}\s*Original Message\s*-{2,}",
    r"-{2,}\s*原始邮件\s*-{2,}",
    r"On\s+.+wrote:",
    r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}.+wrote:",
    r"From:\s+.+",
    r"发件人:\s+.+",
    r"Sent:\s+.+",
    r"发送时间:\s+.+",
    r"To:\s+.+",
    r"收件人:\s+.+",
    r"Subject:\s+.+",
    r"主题:\s+.+",
]

# ── 签名分隔符 ──
SIGNATURE_SEPARATORS = [
    r"^--\s*$",
    r"^__+\s*$",
    r"^Best regards,",
    r"^Thanks,",
    r"^Sincerely,",
    r"^此致",
    r"^敬礼",
    r"^Best,",
    r"^Cheers,",
]

# ── 噪音邮件关键词 ──
NOISE_KEYWORDS = [
    "unsubscribe",
    "退订",
    "newsletter",
    "订阅",
    "auto-reply",
    "自动回复",
    "out of office",
    "不在办公室",
    "delivery status notification",
    "投递状态通知",
    "mail delivery",
    "postmaster",
    "mailer-daemon",
    "noreply",
    "no-reply",
]


class MailCleaner:
    """邮件文本清洗器"""

    @staticmethod
    def html_to_text(html: str) -> str:
        """将 HTML 转为纯文本，保留基本段落结构"""
        if not html:
            return ""
        try:
            soup = BeautifulSoup(html, "lxml")
            # 移除 script/style 标签
            for tag in soup(["script", "style", "head", "meta", "link"]):
                tag.decompose()
            # 用换行分隔块级元素
            for br in soup.find_all("br"):
                br.replace_with("\n")
            text = soup.get_text("\n", strip=True)
            return text
        except Exception:
            # lxml 不可用时回退到 html.parser
            try:
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "head", "meta", "link"]):
                    tag.decompose()
                text = soup.get_text("\n", strip=True)
                return text
            except Exception:
                return html

    @staticmethod
    def trim_reply_history(text: str) -> str:
        """裁剪邮件末尾的回复历史"""
        if not text:
            return text

        for pattern in REPLY_SEPARATORS:
            text = re.split(pattern, text, maxsplit=1, flags=re.IGNORECASE)[0]

        return text.strip()

    @staticmethod
    def trim_signature(text: str) -> str:
        """裁剪邮件末尾的签名块"""
        if not text:
            return text

        # 找最后一个签名分隔符
        best_idx = len(text)
        for pattern in SIGNATURE_SEPARATORS:
            matches = list(re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE))
            if matches:
                idx = matches[-1].start()
                if idx < best_idx:
                    best_idx = idx

        if best_idx < len(text) and best_idx > len(text) * 0.5:
            text = text[:best_idx].strip()

        return text

    @staticmethod
    def clean_whitespace(text: str) -> str:
        """合并多余空白、空行"""
        if not text:
            return text
        # 合并连续空行
        text = re.sub(r"\n{3,}", "\n\n", text)
        # 去掉行首行尾空白
        text = text.strip()
        return text

    @staticmethod
    def is_noise_email(subject: str, from_addr: str, body: str) -> bool:
        """判断是否为噪音邮件（通知、垃圾、自动回复等）"""
        check_text = f"{subject} {from_addr}".lower()
        body_lower = body[:500].lower() if body else ""

        for keyword in NOISE_KEYWORDS:
            if keyword in check_text or keyword in body_lower:
                return True
        return False

    def clean(self, body_text: str, body_html: str = "") -> str:
        """
        主清洗流程：
        1. HTML → 文本 (如果纯文本为空)
        2. 裁剪回复历史
        3. 裁剪签名
        4. 合并空白
        """
        text = body_text.strip() if body_text else ""
        if not text and body_html:
            text = self.html_to_text(body_html)

        if not text:
            return ""

        text = self.trim_reply_history(text)
        text = self.trim_signature(text)
        text = self.clean_whitespace(text)

        return text

    def estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数 (中文 ~1.5 字/token, 英文 ~4 字/token)"""
        chinese_chars = len(re.findall(r"[一-鿿]", text))
        other_chars = len(text) - chinese_chars
        return int(chinese_chars / 1.5 + other_chars / 4)
