"""Classify allowed official sources containing tax; not publication approval."""
from .topics import classify
import re
import unicodedata

def source_is_tax(item,title,reference_text):
    if item['category']=='us-tax': return True
    return item['id'] in ('sba-business-plan','sba-legacy-business-plan-2025') and item['category']=='us-business' and classify(title+' '+reference_text)=='tax_review'


def reject_absolute_tax_exemptions(text):
    """Reject obvious unsupported blanket claims; not a complete fact checker."""
    if not isinstance(text,str): raise ValueError('invalid tax claim text')
    value=unicodedata.normalize('NFC',text).casefold()
    patterns=(r'(?:miễn(?:\s+trừ)?|không\s+(?:phải|cần|chịu)|khỏi)\b[^.!?\n]{0,70}\b(?:mọi|tất\s+cả|toàn\s+bộ|bất\s+kỳ)\b[^.!?\n]{0,50}\bthuế\b',
              r'\b(?:mọi|tất\s+cả|toàn\s+bộ)\b[^.!?\n]{0,50}\bthuế\b[^.!?\n]{0,50}\b(?:miễn|không\s+phải|không\s+chịu)\b',
              r'\b(?:exempt|free|no|without)\b[^.!?\n]{0,70}\b(?:all|any|every)\b[^.!?\n]{0,50}\btax(?:es)?\b',
              r'\b(?:all|any|every)\b[^.!?\n]{0,50}\btax(?:es)?\b[^.!?\n]{0,50}\b(?:exempt|waived|free)\b')
    if any(re.search(pattern,value) for pattern in patterns): raise ValueError('blanket tax exemption claim requires independent source verification')
