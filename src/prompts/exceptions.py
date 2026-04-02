from src.core.exceptions import GradingSystemError


class PromptProviderError(GradingSystemError):
    pass


class PromptAssetNotFound(PromptProviderError):
    pass


class PromptRenderError(PromptProviderError):
    pass


class PromptTokenBudgetExceeded(PromptProviderError):
    pass


class PromptVariableValidationError(PromptProviderError):
    pass
