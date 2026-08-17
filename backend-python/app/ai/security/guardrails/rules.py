"""Stable default heuristic guardrail rules."""

from app.ai.security.guardrails.models import GuardrailAction, GuardrailRule
from app.ai.security.rules_engine import RuleCondition, RuleOperator

DEFAULT_GUARDRAIL_RULES: tuple[GuardrailRule, ...] = (
    GuardrailRule(
        id="prompt-ignore-instructions",
        version=1,
        name="Ignore prior instructions",
        priority=10,
        condition=RuleCondition(
            field="content_text",
            operator=RuleOperator.REGEX,
            value=r"(?i)ignore (?:all |the )?(?:previous|prior|above) instructions",
        ),
        action=GuardrailAction.FLAG,
    ),
    GuardrailRule(
        id="prompt-injection-role-override",
        version=1,
        name="Role override attempt",
        priority=20,
        condition=RuleCondition(
            field="content_text",
            operator=RuleOperator.REGEX,
            value=(
                r"(?i)\byou are now (?:an? |the )?"
                r"(?:unrestricted |different )?(?:assistant|system|admin|developer)\b"
            ),
        ),
        action=GuardrailAction.FLAG,
    ),
    GuardrailRule(
        id="prompt-injection-system-prompt-leak",
        version=1,
        name="System prompt leak attempt",
        priority=30,
        condition=RuleCondition(
            field="content_text",
            operator=RuleOperator.REGEX,
            value=r"(?i)(?:reveal|print|show).{0,20}(?:system prompt|instructions)",
        ),
        action=GuardrailAction.FLAG,
    ),
    GuardrailRule(
        id="secret-like-token-in-content",
        version=1,
        name="Secret-shaped token",
        priority=40,
        condition=RuleCondition(
            field="content_text",
            operator=RuleOperator.REGEX,
            value=r"(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})",
        ),
        action=GuardrailAction.BLOCK,
    ),
    GuardrailRule(
        id="mcp-untrusted-result-shell-marker",
        version=1,
        name="MCP shell marker",
        priority=50,
        condition=RuleCondition(
            all_of=[
                RuleCondition(
                    field="source",
                    operator=RuleOperator.EQ,
                    value="mcp_result",
                ),
                RuleCondition(
                    field="content_text",
                    operator=RuleOperator.REGEX,
                    value=r"(?i)(?:\$\(|`[^`]+`|\b(?:curl|wget)\s+https?://)",
                ),
            ]
        ),
        action=GuardrailAction.FLAG,
    ),
)
