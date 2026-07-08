"""邮件处理模块"""

from .imap_client import IMAPClient, IMAPConfig, create_imap_client_from_settings
from .parser import ParsedEmail, parse_email
from .cleaner import MailCleaner

__all__ = [
    "IMAPClient",
    "IMAPConfig",
    "create_imap_client_from_settings",
    "ParsedEmail",
    "parse_email",
    "MailCleaner",
]
