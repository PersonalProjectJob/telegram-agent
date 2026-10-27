"""The one list of gate codes an Owner override can never cover (writer, approvals and admin_state all read it).

Unsupported facts, unexpected Han text and a medical claim are never overridable. Every other `hold` is structural;
only `fixable` issues outside this set can be overridden by the Owner. Kept in its own module because quality.py
imports writer.py, so writer.py cannot import it from quality.py.
"""

NEVER_OVERRIDABLE = frozenset({"facts", "unexpected_han", "medical_claim"})
