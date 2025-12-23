# AI_RULES.md
## AI-GENT Rules (Simple & Explicit)

Version: 2.0  
Status: ACTIVE  
Applies to:
- mes_scalp.py
- mes_swing.py

AI-GENT exists to safely apply **human-approved changes**.
It is a steward, not a decision-maker.

If a rule cannot be remembered without opening this file,
the rule is too complex and should not exist.

---

## 1. Scope

AI-GENT governs **only** the following files:

- mes_scalp.py
- mes_swing.py

No other files are in scope unless explicitly added by the human operator.

---

## 2. Types of Changes

There are **only two types of changes**.

### A. Logic Changes

A logic change alters:
- execution logic
- control flow
- signal qualification
- strategy behavior

If behavior changes, it is a logic change.

---

### B. Parameter Changes

A parameter change alters:
- numeric values
- thresholds
- limits
- risk percentages
- constants

If no logic or control flow is altered, the change is **parametric**.

---

## 3. Logic Change Requirements

Logic changes require **four items only**:

1. **File name**
2. **Short tagline** (one sentence describing the change)
3. **Version bump**
4. **Optional note** explaining *why* (optional)

No additional fields are required.

### Example

```text
File: mes_scalp.py
Tagline: Relaxed impulse continuation gating
Version: v3.4.8 → v3.4.9
Note: Improves continuation entries during strong HTF alignmenty
