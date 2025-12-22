# MES Versioning Policy

This document defines the official, machine-enforceable versioning policy for the Market Execution System (MES) used by the Leo trading stack.

## Version Format
Versions follow the pattern:  
`vMAJOR.MINOR.PATCH[suffix]`  
- `MAJOR`, `MINOR`, `PATCH`: positive integers  
- `suffix`: optional lowercase letter `a`–`z` (hotfix identifier)

## Definition of "Behavior Change"
A **behavior change** is any modification that can cause **different trades** (entry, exit, size, timing, or cancellation) to be taken under **identical market conditions** and **identical account state**.

## Version Increment Rules

| Change Category                  | Description                                                                                   | Required Increment |
|----------------------------------|-----------------------------------------------------------------------------------------------|--------------------|
| `risk_model_rewrite`             | Complete rewrite of risk model or strategy family (breaks backward compatibility)             | **MAJOR**          |
| `strategy_behavior`              | Intentional change to trade selection, timing, filters, or frequency within same family       | **MINOR**          |
| `execution_logic`                | Changes that only improve robustness, retries, failover, or execution path (no trade impact) | **PATCH**          |
| `diagnostics_logging`            | Logging, metrics, alerts, comments, documentation                                             | **PATCH**          |
| `infrastructure`                | Refactors, dependency updates, config structure, tooling (no runtime trade impact)           | **PATCH**          |
| `hotfix_critical`                | Urgent fix required immediately without widening scope                                        | **SUFFIX** (a-z)   |

### Override Rule
- `strategy_behavior` + `hotfix_critical` → apply **SUFFIX** instead of MINOR  
  (allows emergency behavior fix without jumping to next MINOR when scope must remain narrow)

## Precedence Rule (Collapsed)
When multiple change categories are declared in a single commit, AI-GENT **must** select the **highest-impact** increment:  
**MAJOR > MINOR > PATCH > SUFFIX**  
unless an explicit override above applies.

## Enforcement Requirements for AI-GENT
- Change categories **must** be explicitly declared by the producing agent (never inferred from diffs)
- AI-GENT calculates `version_to` automatically from current tag + declared categories
- AI-GENT enforces strictly monotonic version progression
- AI-GENT blocks any commit where declared categories do not justify the proposed version bump
- Version governance applies **only** to Git tags/branches — never affects live trading execution
- Changes to this policy document itself do **not** trigger MES version increment

## Historical Examples
- `v3.4.8` → `v3.4.8a` : added OANDA retry wrapper (execution_logic + hotfix_critical)
- `v3.4.8a` → `v3.4.9` : tightened RSI reversal filter (strategy_behavior)
- `v3.4.9` → `v3.5.0` : introduced volume confirmation threshold (strategy_behavior)
- `v3.5.0` → `v4.0.0` : complete shift to multi-timeframe momentum family (risk_model_rewrite)

This policy is final and enforced as of 2025-12-21.
