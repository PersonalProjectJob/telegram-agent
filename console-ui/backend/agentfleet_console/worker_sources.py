"""Resolve only the source-bundled, trusted legacy dependencies in one place."""
import sys
from .paths import ROOT

for source in (ROOT/'listener'/'src',ROOT/'nailhub'/'src'):
    if str(source) not in sys.path:sys.path.insert(0,str(source))

from tg_triage.ai_transport import complete, validate_endpoint, ProviderError, read_key
from tg_triage.config import load_config as load_triage
from nailhub.config import load_config as load_nailhub
from tg_triage.model_lock import ModelLock, LockStopped
