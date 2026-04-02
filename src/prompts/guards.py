from src.prompts.exceptions import PromptTokenBudgetExceeded


def validate_budget_or_raise(
    *,
    token_estimate: int,
    max_input_tokens: int,
    reserve_output_tokens: int,
) -> None:
    if max_input_tokens <= 0:
        raise PromptTokenBudgetExceeded("max_input_tokens must be positive")
    if reserve_output_tokens < 0:
        raise PromptTokenBudgetExceeded("reserve_output_tokens cannot be negative")
    budget = max_input_tokens - reserve_output_tokens
    if budget <= 0:
        raise PromptTokenBudgetExceeded("effective prompt budget is non-positive")
    if token_estimate > budget:
        raise PromptTokenBudgetExceeded(
            f"Prompt token budget exceeded: estimated={token_estimate}, budget={budget}"
        )
